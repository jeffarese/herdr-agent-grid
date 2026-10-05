//! Exact provider links only; all discovery and I/O happen off the UI thread.
use crate::{
    model::{Agent, Metrics, Subagent, now},
    telemetry::{ChildHint, Cursor, MAX_READ, first, label, rates},
};
use regex::Regex;
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::{BufRead, BufReader, Read},
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::LazyLock,
    time::{Duration, Instant},
};
use walkdir::WalkDir;

pub fn expand(path: &str) -> PathBuf {
    if path == "~" {
        PathBuf::from(std::env::var("HOME").unwrap_or_default())
    } else if let Some(rest) = path.strip_prefix("~/") {
        PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(rest)
    } else {
        PathBuf::from(path)
    }
}
fn resolved(p: PathBuf) -> PathBuf {
    p.canonicalize().unwrap_or(p)
}
pub fn roots(provider: &str) -> Vec<PathBuf> {
    let home = expand("~");
    let mut dirs = vec![];
    if provider == "claude" {
        dirs.push(expand(
            &std::env::var("CLAUDE_CONFIG_DIR")
                .unwrap_or(home.join(".claude").to_string_lossy().into()),
        ));
        if let Ok(entries) = fs::read_dir(&home) {
            dirs.extend(
                entries
                    .flatten()
                    .filter(|e| e.file_name().to_string_lossy().starts_with(".claude-"))
                    .map(|e| e.path()),
            );
        }
        let extra = std::env::var("HERDR_AGENT_GRID_CLAUDE_DIRS")
            .or_else(|_| std::env::var("HERDR_GRID_CLAUDE_DIRS"))
            .unwrap_or_default();
        dirs.extend(
            std::env::split_paths(&extra)
                .filter(|p| !p.as_os_str().is_empty())
                .map(|p| expand(&p.to_string_lossy())),
        );
        dirs = dirs
            .into_iter()
            .map(|p| resolved(p.join("projects")))
            .collect();
    } else if provider == "codex" {
        let base = expand(
            &std::env::var("CODEX_HOME").unwrap_or(home.join(".codex").to_string_lossy().into()),
        );
        dirs = vec![
            resolved(base.join("sessions")),
            resolved(base.join("archived_sessions")),
        ];
    }
    let mut seen = HashSet::new();
    dirs.retain(|p| seen.insert(p.clone()));
    dirs
}
fn safe_file(path: &Path, root: &Path) -> Option<PathBuf> {
    let p = path.canonicalize().ok()?;
    if p.starts_with(root) && p.is_file() {
        Some(p)
    } else {
        None
    }
}
fn json_files(root: &Path, depth: usize) -> impl Iterator<Item = PathBuf> + '_ {
    WalkDir::new(root)
        .max_depth(depth)
        .follow_links(false)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.path().extension().is_some_and(|s| s == "jsonl"))
        .filter_map(|e| safe_file(e.path(), root))
}
pub fn read_object(path: &Path, limit: u64, first_line: bool) -> Value {
    let Ok(file) = File::open(path) else {
        return Value::Null;
    };
    let mut data = vec![];
    let result = if first_line {
        BufReader::new(file.take(limit + 1)).read_until(b'\n', &mut data)
    } else {
        file.take(limit + 1).read_to_end(&mut data)
    };
    if result.is_err() || data.len() as u64 > limit {
        return Value::Null;
    }
    serde_json::from_slice::<Value>(&data)
        .ok()
        .filter(Value::is_object)
        .unwrap_or(Value::Null)
}
#[derive(Default)]
pub struct Telemetry {
    roots: Option<HashMap<String, Vec<PathBuf>>>,
    cursors: HashMap<String, (PathBuf, Cursor)>,
    misses: HashMap<String, Instant>,
    observed: HashMap<String, (String, u64, f64, f64)>,
    children: Children,
}
impl Telemetry {
    /// Restrict discovery to explicit stores, without changing process environment.
    pub fn with_roots(stores: HashMap<String, Vec<PathBuf>>) -> Self {
        let stores: HashMap<_, Vec<_>> = stores
            .into_iter()
            .map(|(provider, paths)| (provider, paths.into_iter().map(resolved).collect()))
            .collect();
        Self {
            children: Children {
                codex_roots: Some(stores.get("codex").cloned().unwrap_or_default()),
                ..Default::default()
            },
            roots: Some(stores),
            ..Default::default()
        }
    }

    pub fn find(&self, a: &Agent) -> Option<PathBuf> {
        let dirs = self
            .roots
            .as_ref()
            .map(|r| r.get(a.provider()).cloned().unwrap_or_default())
            .unwrap_or_else(|| roots(a.provider()));
        if a.session_kind == "path" {
            let p = expand(&a.session_ref);
            if p.extension().is_some_and(|s| s == "jsonl") {
                for root in dirs {
                    if let Some(p) = safe_file(&p, &root) {
                        return Some(p);
                    }
                }
            }
        } else if a.session_kind == "id" {
            static ID: LazyLock<Regex> = LazyLock::new(|| {
                Regex::new(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$").unwrap()
            });
            if !ID.is_match(&a.session_ref) {
                return None;
            }
            let suffix = format!("{}.jsonl", a.session_ref);
            for root in dirs {
                for p in json_files(
                    &root,
                    if a.provider() == "claude" {
                        2
                    } else {
                        usize::MAX
                    },
                ) {
                    let name = p.file_name()?.to_string_lossy();
                    if (a.provider() == "claude" && name == suffix)
                        || (a.provider() == "codex" && name.ends_with(&suffix))
                    {
                        return Some(p);
                    }
                }
            }
        }
        None
    }
    pub fn forget(&mut self, agents: &[Agent]) {
        let live: HashSet<_> = agents.iter().map(Agent::identity).collect();
        self.cursors.retain(|k, _| live.contains(k));
        self.misses.retain(|k, _| live.contains(k));
        self.observed.retain(|k, _| live.contains(k));
        self.children.groups.retain(|k, _| live.contains(k));
        let used: HashSet<_> = self
            .children
            .groups
            .values()
            .flat_map(|g| g.values().map(|(p, _)| p.with_extension("meta.json")))
            .collect();
        self.children.metadata.retain(|p, _| used.contains(p));
    }
    pub fn read(&mut self, a: &Agent) -> Metrics {
        let key = a.identity();
        let time = now();
        let obs =
            self.observed
                .entry(key.clone())
                .or_insert((a.status.clone(), a.state_seq, time, time));
        if obs.0 != a.status || obs.1 != a.state_seq {
            obs.0 = a.status.clone();
            obs.1 = a.state_seq;
            obs.3 = time;
        }
        let (seen, since) = (obs.2, obs.3);
        if !self.cursors.contains_key(&key)
            && self.misses.get(&key).is_none_or(|t| Instant::now() >= *t)
        {
            if let Some(p) = self.find(a) {
                self.cursors.insert(key.clone(), (p, Cursor::default()));
            } else {
                self.misses
                    .insert(key.clone(), Instant::now() + Duration::from_secs(15));
            }
        }
        let mut m = Metrics::default();
        let mut reason = String::new();
        if let Some((path, c)) = self.cursors.get_mut(&key) {
            let issue = c
                .update_provider(path, a.provider(), rates())
                .err()
                .map(|e| format!("Session metrics unavailable: {e}"));
            m = c.metrics.clone();
            m.session_key = format!("{}:{}", a.provider(), path.display());
            m.issue = issue.unwrap_or_default();
            if !m.issue.is_empty() {
                m.cost_partial = true;
                m.estimate_partial = true;
            }
            m.source = format!("{} session log", a.provider());
            m.subagents = self.children.read(a, path, c);
            reason = c.unpriced_reason().unwrap_or(if m.issue.is_empty() {
                "Dollar total not reported by session".into()
            } else {
                m.issue.clone()
            });
        } else {
            m.source = "Herdr status".into();
        }
        m.seen_at = seen;
        m.status_since = since;
        if m.cost.is_none() && m.estimated_cost.is_none() {
            m.cost_reason = if !reason.is_empty() {
                reason
            } else if a.session_ref.is_empty() {
                "Session reference not reported by Herdr".into()
            } else {
                "No matching local session log".into()
            };
        }
        m
    }
}
#[derive(Clone)]
struct Link {
    id: String,
    parent: String,
    name: String,
    path: PathBuf,
}
type FileStamp = (u64, u64, u64, i64, i64);
fn stamp(s: &fs::Metadata) -> FileStamp {
    (s.dev(), s.ino(), s.len(), s.mtime(), s.mtime_nsec())
}
#[derive(Default)]
struct Children {
    codex_roots: Option<Vec<PathBuf>>,
    groups: HashMap<String, HashMap<String, (PathBuf, Cursor)>>,
    metadata: HashMap<PathBuf, (FileStamp, Value)>,
    headers: HashMap<PathBuf, (FileStamp, Option<Link>)>,
    links: HashMap<String, Vec<Link>>,
    stores: Vec<PathBuf>,
    scan_after: Option<Instant>,
}
impl Children {
    fn meta(&mut self, p: &Path, folder: &Path) -> Value {
        if safe_file(p, folder).is_none() {
            return Value::Null;
        }
        let Ok(stat) = fs::metadata(p) else {
            return Value::Null;
        };
        let key = stamp(&stat);
        if let Some((old, v)) = self.metadata.get(p)
            && *old == key
        {
            return v.clone();
        }
        let v = read_object(p, 32768, false);
        let mut fields = serde_json::Map::new();
        for k in [
            "name",
            "description",
            "agentType",
            "toolUseId",
            "model",
            "effort",
        ] {
            if v[k].is_string() {
                fields.insert(k.into(), v[k].clone());
            }
        }
        let v = Value::Object(fields);
        self.metadata.insert(p.into(), (key, v.clone()));
        v
    }
    fn index_codex(&mut self) {
        let stores = self.codex_roots.clone().unwrap_or_else(|| roots("codex"));
        if stores == self.stores && self.scan_after.is_some_and(|t| Instant::now() < t) {
            return;
        }
        if stores != self.stores {
            self.headers.clear();
        }
        self.stores = stores.clone();
        self.scan_after = Some(Instant::now() + Duration::from_secs(3));
        let mut headers = HashMap::new();
        let mut links: HashMap<String, HashMap<String, Link>> = HashMap::new();
        for root in stores {
            for path in json_files(&root, usize::MAX) {
                let Ok(stat) = fs::metadata(&path) else {
                    continue;
                };
                let key = stamp(&stat);
                let link = if let Some((old, link)) = self
                    .headers
                    .get(&path)
                    .filter(|(old, _)| old.0 == key.0 && old.1 == key.1 && old.2 <= key.2)
                {
                    let _ = old;
                    link.clone()
                } else {
                    let r = read_object(&path, MAX_READ, true);
                    let p = &r["payload"];
                    if r["type"] != "session_meta" || !p.is_object() {
                        continue;
                    }
                    let sub = &p["source"]["subagent"];
                    let spawn = &sub["thread_spawn"];
                    let parent = label(
                        first(&[&p["parent_thread_id"], &spawn["parent_thread_id"]]),
                        128,
                    );
                    let id = label(first(&[&p["id"], &p["session_id"]]), 128);
                    if sub.get("other").is_some()
                        || parent.is_empty()
                        || id.is_empty()
                        || parent == id
                    {
                        None
                    } else {
                        let mut name =
                            label(first(&[&p["agent_nickname"], &spawn["agent_nickname"]]), 80);
                        if name.is_empty() {
                            name = spawn["agent_path"]
                                .as_str()
                                .unwrap_or("")
                                .rsplit('/')
                                .next()
                                .unwrap_or("")
                                .chars()
                                .take(80)
                                .collect();
                        }
                        if name.is_empty() {
                            name = id.chars().take(12).collect();
                        }
                        Some(Link {
                            id,
                            parent,
                            name,
                            path: path.clone(),
                        })
                    }
                };
                if let Some(l) = &link {
                    links
                        .entry(l.parent.clone())
                        .or_default()
                        .insert(l.id.clone(), l.clone());
                }
                headers.insert(path, (key, link));
            }
        }
        self.headers = headers;
        self.links = links
            .into_iter()
            .map(|(p, g)| {
                let mut list: Vec<_> = g.into_values().collect();
                list.sort_by_key(|l| (l.name.to_lowercase(), l.id.clone()));
                (p, list)
            })
            .collect();
    }
    fn read(&mut self, a: &Agent, path: &Path, parent: &mut Cursor) -> Vec<Subagent> {
        let provider = a.provider();
        if !matches!(provider, "claude" | "codex") {
            return vec![];
        }
        let mut children: Vec<(String, String, Option<PathBuf>, ChildHint)> = vec![];
        if provider == "claude" {
            let Ok(parent_path) = path.canonicalize() else {
                return vec![];
            };
            let folder = parent_path.with_extension("").join("subagents");
            if folder.canonicalize().is_ok_and(|p| p != folder) {
                return vec![];
            }
            let mut files = HashMap::new();
            if let Ok(entries) = fs::read_dir(&folder) {
                for entry in entries.flatten() {
                    let p = entry.path();
                    let name = entry.file_name();
                    let name = name.to_string_lossy();
                    if let Some(id) = name
                        .strip_prefix("agent-")
                        .and_then(|s| s.strip_suffix(".jsonl"))
                        && let Some(p) = safe_file(&p, &folder)
                    {
                        files.insert(id.to_owned(), p);
                    }
                }
            }
            let mut ids: Vec<_> = files
                .keys()
                .chain(parent.child_hints.keys())
                .cloned()
                .collect();
            ids.sort();
            ids.dedup();
            for id in ids {
                let path = files.remove(&id);
                let meta = path
                    .as_ref()
                    .map(|p| self.meta(&p.with_extension("meta.json"), &folder))
                    .unwrap_or(Value::Null);
                let mut h = parent
                    .child_hints
                    .get(&id)
                    .or_else(|| {
                        parent
                            .spawn_calls
                            .get(meta["toolUseId"].as_str().unwrap_or(""))
                    })
                    .cloned()
                    .unwrap_or_default();
                let name = label(
                    first(&[
                        &meta["name"],
                        &Value::String(h.name.clone()),
                        &meta["description"],
                        &meta["agentType"],
                    ]),
                    80,
                );
                h.name = name.clone();
                if h.model.is_empty() {
                    h.model = label(&meta["model"], 80);
                }
                if h.effort.is_empty() {
                    h.effort = label(&meta["effort"], 24);
                }
                children.push((id, name, path, h));
            }
        } else {
            if parent.session_id.is_empty() {
                if a.session_kind == "id" {
                    parent.session_id = a.session_ref.clone();
                } else if !parent.session_header_checked {
                    let r = read_object(path, MAX_READ, true);
                    if r["type"] == "session_meta" {
                        parent.session_id = label(
                            first(&[&r["payload"]["id"], &r["payload"]["session_id"]]),
                            128,
                        );
                    }
                    parent.session_header_checked = true;
                }
            }
            self.index_codex();
            let mut pending = vec![parent.session_id.clone()];
            let mut seen = HashSet::from([parent.session_id.clone()]);
            while let Some(id) = pending.pop() {
                if let Some(links) = self.links.get(&id) {
                    for l in links {
                        if seen.insert(l.id.clone()) {
                            pending.push(l.id.clone());
                            children.push((
                                l.id.clone(),
                                l.name.clone(),
                                Some(l.path.clone()),
                                ChildHint::default(),
                            ));
                        }
                    }
                }
            }
        }
        let group = self.groups.entry(a.identity()).or_default();
        let mut unavailable = HashSet::new();
        for (id, _, path, _) in &children {
            if let Some(path) = path {
                let entry = group.entry(id.clone()).or_insert_with(|| {
                    (path.clone(), {
                        let mut c = Cursor::default();
                        c.subagent = true;
                        c
                    })
                });
                if entry.0 != *path {
                    *entry = (path.clone(), {
                        let mut c = Cursor::default();
                        c.subagent = true;
                        c
                    });
                }
                if entry.1.update_provider(path, provider, rates()).is_err() {
                    unavailable.insert(id.clone());
                }
            }
        }
        let mut hints = parent.child_hints.clone();
        if provider == "claude" {
            for (_, c) in group.values() {
                for (id, h) in &c.child_hints {
                    if hints
                        .get(id)
                        .is_none_or(|old| h.event_at.unwrap_or(0.0) > old.event_at.unwrap_or(0.0))
                    {
                        hints.insert(id.clone(), h.clone());
                    }
                }
            }
        }
        let mut result = vec![];
        let child_paths: HashMap<_, _> = children
            .iter()
            .filter_map(|(id, _, path, _)| {
                path.as_ref()
                    .map(|p| (id.clone(), format!("{provider}:{}", p.display())))
            })
            .collect();
        for (id, name, path, mut h) in children {
            let mut descendant_keys = vec![];
            let mut pending = vec![id.clone()];
            let mut seen = HashSet::from([id.clone()]);
            while let Some(parent_id) = pending.pop() {
                let ids: Vec<_> = if provider == "codex" {
                    self.links
                        .get(&parent_id)
                        .into_iter()
                        .flatten()
                        .map(|l| l.id.clone())
                        .collect()
                } else {
                    group
                        .get(&parent_id)
                        .map(|(_, c)| c.child_hints.keys().cloned().collect())
                        .unwrap_or_default()
                };
                for child_id in ids {
                    if seen.insert(child_id.clone()) {
                        if let Some(key) = child_paths.get(&child_id) {
                            descendant_keys.push(key.clone());
                        }
                        pending.push(child_id);
                    }
                }
            }
            descendant_keys.sort();
            if let Some(observed) = hints
                .get(&id)
                .filter(|o| o.event_at.unwrap_or(0.0) >= h.event_at.unwrap_or(0.0))
            {
                let mut o = observed.clone();
                if o.name.is_empty() {
                    o.name = h.name;
                }
                if o.model.is_empty() {
                    o.model = h.model;
                }
                if o.effort.is_empty() {
                    o.effort = h.effort;
                }
                o.started_at = o.started_at.or(h.started_at);
                h = o;
            }
            let cursor = group.get(&id).map(|(_, c)| c);
            let mut m = cursor.map(|c| c.metrics.clone()).unwrap_or_default();
            if unavailable.contains(&id) {
                m.cost_partial = true;
                m.estimate_partial = true;
            }
            let mut status = cursor
                .map(|c| c.lifecycle.clone())
                .filter(|s| !s.is_empty())
                .unwrap_or("unknown".into());
            let mut finished = cursor.and_then(|c| c.finished_at);
            if provider == "claude"
                && h.status != "unknown"
                && h.event_at.unwrap_or(0.0) >= cursor.and_then(|c| c.lifecycle_at).unwrap_or(0.0)
            {
                status = h.status.clone();
                finished = h.finished_at;
            }
            if h.duration_s.is_some() && status == "unknown" {
                status = "done".into();
            }
            result.push(Subagent {
                descendant_keys,
                session_key: path
                    .map(|p| format!("{provider}:{}", p.display()))
                    .unwrap_or_default(),
                id: id.clone(),
                name: if !h.name.is_empty() {
                    h.name
                } else if !name.is_empty() {
                    name
                } else {
                    id
                },
                model: if m.model.is_empty() { h.model } else { m.model },
                effort: if m.effort.is_empty() {
                    h.effort
                } else {
                    m.effort
                },
                cost: m.cost,
                estimated_cost: m.estimated_cost,
                cost_partial: m.cost_partial,
                estimate_partial: m.estimate_partial,
                started_at: h.started_at.or(m.started_at),
                finished_at: finished,
                duration_s: if matches!(status.as_str(), "done" | "failed") {
                    h.duration_s
                } else {
                    None
                },
                status,
            });
        }
        let live: HashSet<_> = result.iter().map(|c| c.id.clone()).collect();
        group.retain(|id, _| live.contains(id));
        let rank = |s: &str| match s {
            "working" => 0,
            "failed" => 2,
            "done" => 3,
            _ => 1,
        };
        result.sort_by(|a, b| {
            rank(&a.status)
                .cmp(&rank(&b.status))
                .then_with(|| {
                    b.finished_at
                        .or(b.started_at)
                        .unwrap_or(0.0)
                        .total_cmp(&a.finished_at.or(a.started_at).unwrap_or(0.0))
                })
                .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
                .then_with(|| a.id.cmp(&b.id))
        });
        result
    }
}
