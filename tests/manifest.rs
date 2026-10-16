use std::collections::HashSet;
use toml_edit::{DocumentMut, Item};

fn manifest() -> DocumentMut {
    std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/herdr-plugin.toml"))
        .unwrap()
        .parse()
        .unwrap()
}
fn strings(item: &Item) -> Vec<String> {
    item.as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_owned())
        .collect()
}
const PLATFORMS: [&str; 3] = ["linux", "macos", "windows"];

#[test]
fn every_platform_gets_exactly_one_build() {
    let doc = manifest();
    assert_eq!(strings(&doc["platforms"]), PLATFORMS);
    let builds = doc["build"].as_array_of_tables().unwrap();
    for platform in PLATFORMS {
        let matching: Vec<_> = builds
            .iter()
            .filter(|b| strings(&b["platforms"]).iter().any(|p| p == platform))
            .collect();
        assert_eq!(matching.len(), 1, "{platform}");
        let command = strings(&matching[0]["command"]);
        let installer = if platform == "windows" {
            "install.ps1"
        } else {
            "install.sh"
        };
        assert!(command.contains(&installer.to_owned()), "{command:?}");
        assert_eq!(command.last().unwrap(), "--prepare-only");
    }
}

/// Herdr rejects duplicate ids even when platforms differ, so actions and panes
/// need one command that works everywhere: the native binary, never a shell.
#[test]
fn actions_and_panes_run_the_native_binary_on_every_platform() {
    let doc = manifest();
    let id = doc["id"].as_str().unwrap();
    for kind in ["actions", "panes"] {
        let mut ids = HashSet::new();
        for item in doc[kind].as_array_of_tables().unwrap() {
            assert!(ids.insert(item["id"].as_str().unwrap().to_owned()));
            assert!(item.get("platforms").is_none());
            let command = strings(&item["command"]);
            assert_eq!(command[0], "./bin/herdr-agent-grid", "{kind}");
        }
    }
    let action = &doc["actions"][0];
    assert_eq!(strings(&action["command"])[1..], ["open"]);
    assert_eq!(id, "herdr-agent-grid");
    assert_eq!(doc["panes"][0]["id"].as_str(), Some("grid"));
}
