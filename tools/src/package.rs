use flate2::{Compression, GzBuilder};
use grid_tools::{Result, checked, root};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet},
    fs,
    path::Path,
    process::Command,
};

pub fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub fn archive(files: &BTreeMap<String, (Vec<u8>, u32)>, path: &Path) -> Result<()> {
    let zip = GzBuilder::new()
        .mtime(0)
        .write(fs::File::create(path)?, Compression::default());
    let mut tar = tar::Builder::new(zip);
    for (name, (data, mode)) in files {
        let mut header = tar::Header::new_gnu();
        header.set_size(data.len() as u64);
        header.set_mode(*mode);
        header.set_uid(0);
        header.set_gid(0);
        header.set_mtime(0);
        tar.append_data(
            &mut header,
            format!("herdr-agent-grid/{name}"),
            data.as_slice(),
        )?;
    }
    tar.into_inner()?.finish()?;
    Ok(())
}

fn notices(target: &str) -> Result<Vec<u8>> {
    let cargo = std::env::var_os("CARGO").unwrap_or("cargo".into());
    let metadata: Value = serde_json::from_slice(
        &checked(Command::new(cargo).current_dir(root()).args([
            "metadata",
            "--locked",
            "--format-version",
            "1",
            "--filter-platform",
            target,
        ]))?
        .stdout,
    )?;
    let packages = metadata["packages"]
        .as_array()
        .ok_or("Missing Cargo packages")?;
    let app = packages
        .iter()
        .find(|p| p["name"] == "herdr-agent-grid")
        .ok_or("Missing application package")?;
    let mut needed = HashSet::new();
    let mut pending = vec![app["id"].as_str().unwrap().to_owned()];
    let nodes = metadata["resolve"]["nodes"]
        .as_array()
        .ok_or("Missing dependency graph")?;
    while let Some(id) = pending.pop() {
        if !needed.insert(id.clone()) {
            continue;
        }
        let node = nodes
            .iter()
            .find(|n| n["id"] == id)
            .ok_or("Missing dependency node")?;
        for dep in node["deps"].as_array().ok_or("Missing dependencies")? {
            if dep["dep_kinds"]
                .as_array()
                .unwrap()
                .iter()
                .any(|k| k["kind"].is_null())
            {
                pending.push(dep["pkg"].as_str().unwrap().into());
            }
        }
    }
    let mut selected: Vec<_> = packages
        .iter()
        .filter(|p| needed.contains(p["id"].as_str().unwrap()) && !p["source"].is_null())
        .collect();
    selected.sort_by_key(|p| (p["name"].as_str().unwrap(), p["version"].as_str().unwrap()));
    let mut text = "Third-party software bundled in Herdr Agent Grid\n".to_owned();
    for p in selected {
        text += &format!(
            "\n{} {}\nLicense: {}\n{}\n",
            p["name"].as_str().unwrap(),
            p["version"].as_str().unwrap(),
            p["license"].as_str().unwrap_or("See license files"),
            p["repository"].as_str().unwrap_or("")
        );
        let dir = Path::new(p["manifest_path"].as_str().unwrap())
            .parent()
            .unwrap();
        let mut paths: Vec<_> = fs::read_dir(dir)?.collect::<std::io::Result<Vec<_>>>()?;
        paths.sort_by_key(|e| e.file_name());
        for entry in paths {
            let name = entry.file_name().to_string_lossy().into_owned();
            if entry.file_type()?.is_file()
                && ["LICENSE", "LICENCE", "COPYING", "NOTICE"]
                    .iter()
                    .any(|prefix| name.starts_with(prefix))
            {
                text += &format!(
                    "{name}\n{}\n",
                    String::from_utf8_lossy(&fs::read(entry.path())?)
                );
            }
        }
    }
    Ok(text.into_bytes())
}

pub const TARGETS: [&str; 6] = [
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-unknown-linux-gnu",
    "aarch64-pc-windows-msvc",
    "x86_64-pc-windows-msvc",
];

/// Installer for a release target: PowerShell on Windows, POSIX shell elsewhere.
pub fn installer(target: &str) -> &'static str {
    if target.contains("-windows-") {
        "install.ps1"
    } else {
        "install.sh"
    }
}

pub fn run(args: &[String]) -> Result<()> {
    let mut target = None;
    let mut binary = None;
    let mut out = root().join("dist");
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--target" => target = Some(it.next().ok_or("Missing target")?.clone()),
            "--binary" => binary = Some(it.next().ok_or("Missing binary")?.into()),
            "--output" => out = it.next().ok_or("Missing output")?.into(),
            _ => return Err(format!("Unknown package argument: {arg}").into()),
        }
    }
    let version = herdr_agent_grid::VERSION;
    let manifest =
        fs::read_to_string(root().join("herdr-plugin.toml"))?.parse::<toml_edit::DocumentMut>()?;
    if manifest["version"].as_str() != Some(version) {
        return Err("Manifest version differs from Cargo version".into());
    }
    let mut files = BTreeMap::new();
    if let Some(target) = &target {
        if !TARGETS.contains(&target.as_str()) {
            return Err("Unsupported release target".into());
        }
        let exe = if target.contains("-windows-") {
            "herdr-agent-grid.exe"
        } else {
            "herdr-agent-grid"
        };
        let binary = binary.unwrap_or(root().join("target/release").join(exe));
        files.insert(format!("bin/{exe}"), (fs::read(binary)?, 0o755));
        for name in [
            "herdr-plugin.toml",
            installer(target),
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
        ] {
            let mut data = fs::read(root().join(name))?;
            if name == "README.md" {
                let mut text = String::from_utf8(data)?;
                for dir in ["docs/", "benchmarks/"] {
                    let raw = format!(
                        "https://raw.githubusercontent.com/jeffarese/herdr-agent-grid/v{version}/{dir}"
                    );
                    let page = format!(
                        "https://github.com/jeffarese/herdr-agent-grid/blob/v{version}/{dir}"
                    );
                    text = text.replace(&format!("src=\"{dir}"), &format!("src=\"{raw}"));
                    text = text.replace(&format!("href=\"{dir}"), &format!("href=\"{page}"));
                    // Markdown images need raw content, ordinary links need the repository page.
                    text = text
                        .lines()
                        .map(|line| {
                            if line.trim_start().starts_with("![") {
                                line.replace(&format!("]({dir}"), &format!("]({raw}"))
                            } else {
                                line.replace(&format!("]({dir}"), &format!("]({page}"))
                            }
                        })
                        .collect::<Vec<_>>()
                        .join("\n");
                }
                data = text.into_bytes();
            }
            files.insert(
                name.into(),
                (data, if name.ends_with(".sh") { 0o755 } else { 0o644 }),
            );
        }
        files.insert("THIRD-PARTY-NOTICES.txt".into(), (notices(target)?, 0o644));
    } else {
        // Take executable bits from the index so archives match on every OS.
        let output = checked(
            Command::new("git")
                .current_dir(root())
                .args(["ls-files", "--stage", "-z"]),
        )?;
        for entry in output.stdout.split(|b| *b == 0).filter(|s| !s.is_empty()) {
            let (stage, name) = std::str::from_utf8(entry)?
                .split_once('\t')
                .ok_or("Unexpected git ls-files output")?;
            let path = root().join(name);
            if !path.exists() {
                continue;
            }
            let meta = fs::symlink_metadata(&path)?;
            if !meta.is_file() {
                return Err(format!("Source input must be a regular file: {name}").into());
            }
            let mode = if stage.starts_with("100755") {
                0o755
            } else {
                0o644
            };
            files.insert(name.into(), (fs::read(path)?, mode));
        }
        for required in ["src/main.rs", "tools/src/main.rs", ".cargo/config.toml"] {
            if !files.contains_key(required) {
                return Err(format!("Source archive requires Git-tracked {required}; commit or stage the source changes first").into());
            }
        }
    }
    fs::create_dir_all(&out)?;
    let name = format!(
        "herdr-agent-grid-v{version}-{}.tar.gz",
        target.as_deref().unwrap_or("source")
    );
    let path = out.join(&name);
    archive(&files, &path)?;
    let digest = hash(&fs::read(&path)?);
    fs::write(
        out.join(format!("{name}.sha256")),
        format!("{digest}  {name}\n"),
    )?;
    println!("{}", path.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    #[test]
    fn archives_are_deterministic_with_executable_modes() {
        let dir = tempfile::tempdir().unwrap();
        let files = BTreeMap::from([
            ("install.sh".into(), (b"#!/bin/sh\n".to_vec(), 0o755)),
            ("README.md".into(), (b"hello\n".to_vec(), 0o644)),
        ]);
        let a = dir.path().join("a.tgz");
        let b = dir.path().join("b.tgz");
        archive(&files, &a).unwrap();
        archive(&files, &b).unwrap();
        assert_eq!(fs::read(&a).unwrap(), fs::read(&b).unwrap());
        let mut tar = tar::Archive::new(flate2::read::GzDecoder::new(fs::File::open(a).unwrap()));
        for entry in tar.entries().unwrap() {
            let mut entry = entry.unwrap();
            let name = entry
                .path()
                .unwrap()
                .strip_prefix("herdr-agent-grid")
                .unwrap()
                .to_string_lossy()
                .into_owned();
            let mut data = vec![];
            entry.read_to_end(&mut data).unwrap();
            assert_eq!(data, files[&name].0);
            assert_eq!(entry.header().mode().unwrap(), files[&name].1);
            assert_eq!(entry.header().mtime().unwrap(), 0);
        }
    }
    #[test]
    fn every_release_target_ships_its_platform_installer() {
        for target in TARGETS {
            let expected = if target.ends_with("-windows-msvc") {
                "install.ps1"
            } else {
                "install.sh"
            };
            assert_eq!(installer(target), expected);
            assert!(root().join(expected).is_file());
        }
    }
}
