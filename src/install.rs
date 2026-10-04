use crate::client::{Result, herdr_bin};
use std::{
    env,
    fs::{self, OpenOptions},
    io::Write,
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use toml_edit::{DocumentMut, Item, value};
const ACTION: &str = "herdr-agent-grid.open";
const LEGACY: &str = "herdr-grid.open";
const KEYS: [&str; 3] = ["cmd+g", "prefix+a", "ctrl+alt+g"];
pub fn config_with_shortcut(text: &str) -> Result<(String, Option<String>)> {
    let mut doc = text
        .parse::<DocumentMut>()
        .map_err(|e| format!("Invalid Herdr config: {e}"))?;
    let mut already = false;
    let mut taken = std::collections::HashSet::new();
    if let Some(keys) = doc.get_mut("keys").and_then(Item::as_table_like_mut) {
        for (name, item) in keys.iter_mut() {
            if name == "command" {
                if let Some(commands) = item.as_array_of_tables_mut() {
                    for t in commands.iter_mut() {
                        if t.get("type").and_then(Item::as_str) == Some("plugin_action") {
                            if t.get("command").and_then(Item::as_str) == Some(LEGACY) {
                                let decor = t["command"].as_value().unwrap().decor().clone();
                                t["command"] = value(ACTION);
                                *t["command"].as_value_mut().unwrap().decor_mut() = decor;
                            }
                            if t.get("command").and_then(Item::as_str) == Some(ACTION) {
                                already = true;
                            }
                        }
                        if let Some(key) = t.get("key") {
                            if let Some(s) = key.as_str() {
                                taken.insert(s.to_owned());
                            }
                            if let Some(list) = key.as_array() {
                                for s in list.iter().filter_map(toml_edit::Value::as_str) {
                                    taken.insert(s.to_owned());
                                }
                            }
                        }
                    }
                } else {
                    return Err("Unsupported keys.command format".into());
                }
            } else if let Some(key) = item.as_str() {
                taken.insert(key.to_owned());
            }
        }
    }
    if already {
        return Ok((doc.to_string(), None));
    }
    for key in KEYS {
        if taken.contains(key) {
            return Err(format!("Shortcut {key} is already bound"));
        }
    }
    let result = format!(
        "{}\n\n# Agent Grid: full-panel agent status cards\n[[keys.command]]\nkey = [\"cmd+g\", \"prefix+a\", \"ctrl+alt+g\"]\ntype = \"plugin_action\"\ncommand = \"herdr-agent-grid.open\"\ndescription = \"agent grid\"\n",
        text.trim_end()
    );
    result.parse::<DocumentMut>().map_err(|e| e.to_string())?;
    Ok((result, Some("Cmd+G, prefix then A, or Ctrl+Alt+G".into())))
}
fn plugin_root() -> Result<PathBuf> {
    let exe = env::current_exe()
        .map_err(|e| e.to_string())?
        .canonicalize()
        .map_err(|e| e.to_string())?;
    for dir in exe.ancestors().skip(1).take(4) {
        if dir.join("herdr-plugin.toml").is_file() {
            return Ok(dir.to_path_buf());
        }
    }
    Err("Cannot find herdr-plugin.toml beside this installation".into())
}
fn invoke(args: &[&str], config: &Path) -> Result<()> {
    let (ok, out, err) = crate::client::command_env(
        &herdr_bin(),
        args,
        Duration::from_secs(20),
        Some(("HERDR_CONFIG_PATH", config.as_os_str())),
    )?;
    if ok {
        if !out.is_empty() {
            print!("{}", String::from_utf8_lossy(&out));
        }
        Ok(())
    } else {
        Err(String::from_utf8_lossy(&err).trim().into())
    }
}
pub fn run(args: &[String]) -> Result<()> {
    let mut config = env::var("HERDR_CONFIG_PATH")
        .map(PathBuf::from)
        .unwrap_or(crate::sessions::expand("~/.config/herdr/config.toml"));
    let mut open = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--config" => config = crate::sessions::expand(it.next().ok_or("Missing config path")?),
            "--open" => open = true,
            _ => return Err(format!("Unknown install argument: {a}")),
        }
    }
    // Follow an existing dotfiles symlink to preserve its target.
    if fs::symlink_metadata(&config).is_ok() {
        config = config.canonicalize().map_err(|e| e.to_string())?;
    } else if !config.is_absolute() {
        config = env::current_dir().map_err(|e| e.to_string())?.join(config);
    }
    let old = match fs::read_to_string(&config) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
        Err(e) => return Err(e.to_string()),
    };
    let (new, shortcut) = config_with_shortcut(&old)?;
    let root = plugin_root()?;
    invoke(
        &[
            "plugin",
            "link",
            root.to_str().ok_or("Invalid plugin path")?,
            "--enabled",
        ],
        &config,
    )?;
    if new != old {
        write_config(&config, &old, &new)?;
    }
    invoke(&["server", "reload-config"], &config)?;
    println!(
        "Herdr Agent Grid {} installed. {}",
        crate::VERSION,
        shortcut
            .map(|s| format!("Open with {s}."))
            .unwrap_or("Existing shortcut preserved.".into())
    );
    if open {
        invoke(
            &["plugin", "action", "invoke", "herdr-agent-grid.open"],
            &config,
        )?;
    }
    Ok(())
}
pub fn write_config(path: &Path, old: &str, new: &str) -> Result<()> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temp = parent.join(format!(".grid-config-{}-{stamp}", std::process::id()));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)
            .map_err(|e| e.to_string())?;
        if path.exists() {
            let permissions = fs::metadata(path).map_err(|e| e.to_string())?.permissions();
            file.set_permissions(permissions)
                .map_err(|e| e.to_string())?;
        }
        file.write_all(new.as_bytes())
            .and_then(|_| file.sync_all())
            .map_err(|e| e.to_string())?;
        if fs::read_to_string(path).unwrap_or_default() != old {
            return Err(
                "Herdr config changed during installation; retry without overwriting it".into(),
            );
        }
        if path.exists() {
            let backup = path.with_file_name(format!(
                "{}.bak-grid-{stamp}",
                path.file_name().unwrap().to_string_lossy()
            ));
            fs::copy(path, &backup).map_err(|e| e.to_string())?;
            println!("Config backup: {}", backup.display());
        }
        fs::rename(&temp, path).map_err(|e| e.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}
