use serde_json::{Value, json};
use std::{
    env, fs,
    io::{Read, Write},
    os::unix::{fs::PermissionsExt, net::UnixStream},
    path::PathBuf,
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};
const LIMIT: usize = 32 * 1024 * 1024;
pub type Result<T> = std::result::Result<T, String>;
#[derive(Clone)]
pub struct Client {
    pub socket_path: Option<PathBuf>,
    pub bin: String,
    pub timeout: Duration,
}
impl Default for Client {
    fn default() -> Self {
        Self {
            socket_path: env::var_os("HERDR_SOCKET_PATH")
                .filter(|s| !s.is_empty())
                .map(PathBuf::from),
            bin: herdr_bin(),
            timeout: Duration::from_secs(2),
        }
    }
}
pub fn herdr_bin() -> String {
    env::var("HERDR_BIN_PATH")
        .ok()
        .filter(|p| {
            fs::metadata(p).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
        })
        .unwrap_or("herdr".into())
}
/// Bounded CLI transport, including both pipe readers. Never retries mutations.
pub fn command(bin: &str, args: &[&str], timeout: Duration) -> Result<(bool, Vec<u8>, Vec<u8>)> {
    command_env(bin, args, timeout, None)
}
pub fn command_env(
    bin: &str,
    args: &[&str],
    timeout: Duration,
    env: Option<(&str, &std::ffi::OsStr)>,
) -> Result<(bool, Vec<u8>, Vec<u8>)> {
    let mut command = Command::new(bin);
    command.args(args);
    if let Some((key, value)) = env {
        command.env(key, value);
    }
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| e.to_string())?;
    let out = child.stdout.take().unwrap();
    let err = child.stderr.take().unwrap();
    let (tx, rx) = std::sync::mpsc::channel();
    for (kind, pipe) in [
        (0, Box::new(out) as Box<dyn Read + Send>),
        (1, Box::new(err) as Box<dyn Read + Send>),
    ] {
        let tx = tx.clone();
        thread::spawn(move || {
            let mut data = vec![];
            let result = pipe
                .take(LIMIT as u64 + 1)
                .read_to_end(&mut data)
                .map(|_| data)
                .map_err(|e| e.to_string());
            let _ = tx.send((kind, result));
        });
    }
    drop(tx);
    let deadline = Instant::now() + timeout;
    let mut output = [None, None];
    let mut status = None;
    loop {
        while let Ok((i, data)) = rx.try_recv() {
            if data.as_ref().is_ok_and(|v| v.len() > LIMIT) {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Herdr response exceeds 32 MiB".into());
            }
            output[i] = Some(data);
        }
        if status.is_none() {
            status = child.try_wait().map_err(|e| e.to_string())?;
        }
        if let Some(status) = status
            && output.iter().all(Option::is_some)
        {
            return Ok((
                status.success(),
                output[0].take().unwrap()?,
                output[1].take().unwrap()?,
            ));
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Herdr request timed out".into());
        }
        thread::sleep(Duration::from_millis(2));
    }
}
impl Client {
    pub fn call(&self, method: &str, params: Value, cli: &[&str]) -> Result<Value> {
        let raw = if let Some(path) = &self.socket_path {
            let mut conn = UnixStream::connect(path).map_err(|e| format!("Socket connect: {e}"))?;
            conn.set_write_timeout(Some(self.timeout))
                .map_err(|e| format!("Socket write timeout: {e}"))?;
            // Set options before sending: macOS can reject SO_RCVTIMEO changes
            // after a fast peer has already written its reply and closed.
            // Short blocking reads preserve a total deadline without changing
            // socket options while the response is in flight.
            conn.set_read_timeout(Some(self.timeout.min(Duration::from_millis(50))))
                .map_err(|e| format!("Socket read timeout: {e}"))?;
            let mut request =
                serde_json::to_vec(&json!({"id":"grid","method":method,"params":params})).unwrap();
            request.push(b'\n');
            conn.write_all(&request)
                .map_err(|e| format!("Socket write: {e}"))?;
            let deadline = Instant::now() + self.timeout;
            let mut data = vec![];
            let mut block = [0; 65536];
            loop {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err("Herdr request timed out".into());
                }
                let n = match conn.read(&mut block) {
                    Ok(n) => n,
                    Err(e)
                        if matches!(
                            e.kind(),
                            std::io::ErrorKind::WouldBlock
                                | std::io::ErrorKind::TimedOut
                                | std::io::ErrorKind::Interrupted
                        ) =>
                    {
                        continue;
                    }
                    Err(e) => return Err(format!("Socket read: {e}")),
                };
                if n == 0 {
                    return Err("Herdr closed the connection".into());
                }
                if let Some(end) = block[..n].iter().position(|b| *b == b'\n') {
                    data.extend_from_slice(&block[..end]);
                    break;
                }
                data.extend_from_slice(&block[..n]);
                if data.len() > LIMIT {
                    return Err("Herdr response exceeds 32 MiB".into());
                }
            }
            data
        } else {
            let (ok, out, err) = command(&self.bin, cli, self.timeout)?;
            if ok { out } else { err }
        };
        if raw.len() > LIMIT {
            return Err("Herdr response exceeds 32 MiB".into());
        }
        let response: Value = serde_json::from_slice(&raw).map_err(|_| {
            crate::model::clean(&String::from_utf8_lossy(&raw))
                .chars()
                .take(500)
                .collect::<String>()
        })?;
        if let Some(error) = response.get("error").filter(|e| !e.is_null()) {
            return Err(error["message"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or(error.to_string()));
        }
        response
            .get("result")
            .filter(|v| v.is_object())
            .cloned()
            .ok_or("Herdr response is missing its result".into())
    }
    pub fn snapshot(&self) -> Result<Value> {
        self.call("session.snapshot", json!({}), &["api", "snapshot"])?
            .get("snapshot")
            .filter(|v| v.is_object())
            .cloned()
            .ok_or("Herdr response is missing the snapshot".into())
    }
    pub fn read(&self, id: &str, lines: usize) -> Result<String> {
        self.call(
            "pane.read",
            json!({"pane_id":id,"source":"visible","format":"text","lines":lines}),
            &[
                "pane",
                "read",
                id,
                "--source",
                "visible",
                "--lines",
                &lines.to_string(),
            ],
        )?["read"]["text"]
            .as_str()
            .map(str::to_owned)
            .ok_or("Herdr response is missing the terminal preview".into())
    }
    pub fn focus(&self, id: &str) -> Result<()> {
        self.call("agent.focus", json!({"target":id}), &["agent", "focus", id])
            .map(|_| ())
    }
}
