//! Operating-system differences: home and config locations, executables and file identity.
use std::{
    env,
    ffi::OsString,
    fs::{self, File},
    io,
    path::{Path, PathBuf},
    time::SystemTime,
};

/// Device and file number. Survives renames and changes when a file is replaced.
pub type FileId = (u64, u64);
/// File identity, length and modification time.
pub type FileStamp = (FileId, u64, Option<SystemTime>);

fn var(key: &str) -> Option<OsString> {
    env::var_os(key).filter(|v| !v.is_empty())
}

/// The home directory agents use for their stores (`os.homedir()` on Windows).
pub fn home() -> PathBuf {
    let home = if cfg!(windows) {
        var("USERPROFILE").or_else(|| var("HOME"))
    } else {
        var("HOME")
    };
    home.map(PathBuf::from).unwrap_or_default()
}

/// Herdr's default config file, honoring `HERDR_CONFIG_PATH`.
pub fn herdr_config() -> PathBuf {
    if let Some(path) = var("HERDR_CONFIG_PATH") {
        return path.into();
    }
    if cfg!(windows)
        && let Some(appdata) = var("APPDATA")
    {
        return PathBuf::from(appdata).join("herdr").join("config.toml");
    }
    home().join(".config").join("herdr").join("config.toml")
}

/// Per-user and system font directories.
pub fn font_dirs() -> Vec<PathBuf> {
    let home = home();
    if cfg!(target_os = "macos") {
        vec![home.join("Library/Fonts"), "/Library/Fonts".into()]
    } else if cfg!(windows) {
        let mut dirs = vec![];
        if let Some(local) = var("LOCALAPPDATA") {
            dirs.push(PathBuf::from(local).join(r"Microsoft\Windows\Fonts"));
        }
        dirs.push(PathBuf::from(var("WINDIR").unwrap_or(r"C:\Windows".into())).join("Fonts"));
        dirs
    } else {
        vec![
            var("XDG_DATA_HOME")
                .map(PathBuf::from)
                .unwrap_or(home.join(".local/share"))
                .join("fonts"),
            home.join(".fonts"),
            "/usr/local/share/fonts".into(),
            "/usr/share/fonts".into(),
        ]
    }
}

#[cfg(unix)]
pub fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    fs::metadata(path).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

/// Windows has no executable bit; `CreateProcess` decides by file type.
#[cfg(not(unix))]
pub fn is_executable(path: &Path) -> bool {
    path.is_file()
}

/// Drop the Windows verbatim prefix (`\\?\C:\…`) that `canonicalize` adds, so
/// paths stay readable and acceptable to programs that do not support it.
pub fn simplify(path: PathBuf) -> PathBuf {
    if cfg!(windows)
        && let Some(s) = path.to_str()
    {
        if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
            return format!(r"\\{rest}").into();
        }
        if let Some(rest) = s.strip_prefix(r"\\?\")
            && rest.as_bytes().get(1) == Some(&b':')
        {
            return rest.into();
        }
    }
    path
}

#[cfg(unix)]
pub fn identity(_file: &File, meta: &fs::Metadata) -> io::Result<FileId> {
    use std::os::unix::fs::MetadataExt;
    Ok((meta.dev(), meta.ino()))
}

#[cfg(windows)]
pub fn identity(file: &File, _meta: &fs::Metadata) -> io::Result<FileId> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, GetFileInformationByHandle,
    };
    // SAFETY: an all-zero BY_HANDLE_FILE_INFORMATION is a valid plain-data value.
    let mut info: BY_HANDLE_FILE_INFORMATION = unsafe { std::mem::zeroed() };
    // SAFETY: the handle is owned by `file` and stays open for this call; `info`
    // is a valid, writable out pointer.
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok((
        info.dwVolumeSerialNumber.into(),
        (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
    ))
}

/// Identity, length and modification time without reading the file.
pub fn stamp(path: &Path) -> io::Result<FileStamp> {
    #[cfg(unix)]
    let (id, meta) = {
        use std::os::unix::fs::MetadataExt;
        let meta = fs::metadata(path)?;
        ((meta.dev(), meta.ino()), meta)
    };
    // Windows exposes the file index only through an open handle.
    #[cfg(windows)]
    let (id, meta) = {
        let file = File::open(path)?;
        let meta = file.metadata()?;
        (identity(&file, &meta)?, meta)
    };
    Ok((id, meta.len(), meta.modified().ok()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn identity_follows_the_file_not_the_name() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("log.jsonl");
        fs::write(&path, "a\n").unwrap();
        let first = stamp(&path).unwrap();
        fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(b"b\n")
            .unwrap();
        let grown = stamp(&path).unwrap();
        assert_eq!(grown.0, first.0);
        assert_eq!(grown.1, 4);
        let file = File::open(&path).unwrap();
        assert_eq!(identity(&file, &file.metadata().unwrap()).unwrap(), first.0);
        drop(file);
        let next = dir.path().join("next");
        fs::write(&next, "c\n").unwrap();
        fs::rename(&next, &path).unwrap();
        assert_ne!(stamp(&path).unwrap().0, first.0);
    }

    #[test]
    fn verbatim_prefixes_are_removed_on_windows_only() {
        let drive = PathBuf::from(r"\\?\C:\Users\me\plugin");
        let unc = PathBuf::from(r"\\?\UNC\server\share\plugin");
        let device = PathBuf::from(r"\\?\Volume{1}\plugin");
        if cfg!(windows) {
            assert_eq!(simplify(drive), PathBuf::from(r"C:\Users\me\plugin"));
            assert_eq!(simplify(unc), PathBuf::from(r"\\server\share\plugin"));
        } else {
            assert_eq!(simplify(drive.clone()), drive);
            assert_eq!(simplify(unc.clone()), unc);
        }
        assert_eq!(simplify(device.clone()), device);
        assert_eq!(simplify("/tmp/x".into()), PathBuf::from("/tmp/x"));
    }
}
