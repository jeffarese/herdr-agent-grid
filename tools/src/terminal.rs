//! A real pseudoterminal for integration tests and synthetic benchmarks.
use std::{
    fs::File,
    io::{self, Read, Write},
    os::fd::{AsRawFd, FromRawFd},
    os::unix::process::CommandExt,
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};
pub struct Terminal {
    pub child: Child,
    master: File,
    pub output: Vec<u8>,
}
impl Terminal {
    pub fn spawn(command: &mut Command, width: u16, height: u16) -> io::Result<Self> {
        let (mut master, mut slave) = (0, 0);
        let mut size = libc::winsize {
            ws_row: height,
            ws_col: width,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        // SAFETY: openpty writes two valid file descriptors and reads the supplied size.
        if unsafe {
            libc::openpty(
                &mut master,
                &mut slave,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut size,
            )
        } != 0
        {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: successful openpty transfers ownership of these descriptors to File.
        let (master, slave) = unsafe { (File::from_raw_fd(master), File::from_raw_fd(slave)) };
        command
            .stdin(Stdio::from(slave.try_clone()?))
            .stdout(Stdio::from(slave.try_clone()?))
            .stderr(Stdio::from(slave));
        command
            .env("TERM", "xterm-256color")
            .env_remove("COLUMNS")
            .env_remove("LINES");
        // SAFETY: the closure only calls async-signal-safe libc functions before exec.
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() < 0 || libc::ioctl(0, libc::TIOCSCTTY as _, 0) < 0 {
                    return Err(io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let child = command.spawn()?;
        Ok(Self {
            child,
            master,
            output: vec![],
        })
    }
    pub fn send(&mut self, bytes: &[u8]) -> io::Result<()> {
        self.master.write_all(bytes)
    }
    pub fn read(&mut self, timeout: Duration) -> io::Result<()> {
        let mut fd = libc::pollfd {
            fd: self.master.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        // SAFETY: poll receives one valid pollfd and does not retain its pointer.
        let result =
            unsafe { libc::poll(&mut fd, 1, timeout.as_millis().min(i32::MAX as u128) as i32) };
        if result < 0 {
            let e = io::Error::last_os_error();
            if e.kind() != io::ErrorKind::Interrupted {
                return Err(e);
            }
        }
        if result > 0 && fd.revents & libc::POLLIN != 0 {
            let mut buf = [0; 65536];
            match self.master.read(&mut buf) {
                Ok(n) => self.output.extend_from_slice(&buf[..n]),
                Err(e) if e.raw_os_error() == Some(libc::EIO) => {}
                Err(e) => return Err(e),
            }
        }
        Ok(())
    }
    pub fn wait_for(
        &mut self,
        timeout: Duration,
        mut predicate: impl FnMut(&mut Self) -> bool,
    ) -> io::Result<()> {
        let start = Instant::now();
        loop {
            self.read(Duration::from_millis(10))?;
            if predicate(self) {
                return Ok(());
            }
            if start.elapsed() > timeout {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    format!(
                        "terminal timed out: {}",
                        String::from_utf8_lossy(
                            &self.output[self.output.len().saturating_sub(3000)..]
                        )
                    ),
                ));
            }
        }
    }
    pub fn contains(&self, text: &str) -> bool {
        String::from_utf8_lossy(&self.output).contains(text)
    }
    pub fn traces(&self) -> Vec<serde_json::Value> {
        self.output
            .split(|b| *b == 7)
            .filter_map(|part| {
                let marker = b"\x1b]777;";
                let pos = part.windows(marker.len()).rposition(|w| w == marker)?;
                serde_json::from_slice(&part[pos + marker.len()..]).ok()
            })
            .collect()
    }
    pub fn resize(&self, width: u16, height: u16) -> io::Result<()> {
        let size = libc::winsize {
            ws_row: height,
            ws_col: width,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        // SAFETY: ioctl reads a valid winsize for an open PTY descriptor.
        if unsafe { libc::ioctl(self.master.as_raw_fd(), libc::TIOCSWINSZ, &size) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}
impl Drop for Terminal {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
