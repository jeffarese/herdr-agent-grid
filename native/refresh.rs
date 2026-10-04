use crate::{
    client::Client,
    model::{State, agents_from, clean, now},
    sessions::Telemetry,
    telemetry::screen_call,
};
use std::{
    collections::{HashMap, HashSet},
    sync::{Arc, Condvar, Mutex},
    thread,
    time::Duration,
};
#[derive(Default)]
struct Shared {
    state: Arc<State>,
    targets: HashMap<String, usize>,
    wake: bool,
    stop: bool,
}
pub struct Refresher {
    shared: Arc<(Mutex<Shared>, Condvar)>,
}
impl Refresher {
    pub fn start(client: Client) -> Self {
        Self::start_with_wakeup(client, || {})
    }
    pub fn start_with_wakeup(client: Client, wakeup: impl Fn() + Send + Sync + 'static) -> Self {
        let notify = Arc::new(wakeup);
        let shared = Arc::new((Mutex::new(Shared::default()), Condvar::new()));
        let worker = shared.clone();
        thread::spawn(move || {
            let mut telemetry = Telemetry::default();
            loop {
                {
                    let mut g = worker.0.lock().unwrap();
                    if g.stop {
                        return;
                    }
                    g.wake = false;
                }
                match client.snapshot() {
                    Ok(snapshot) => {
                        let agents = agents_from(&snapshot);
                        telemetry.forget(&agents);
                        let targets = {
                            let mut g = worker.0.lock().unwrap();
                            let state = Arc::make_mut(&mut g.state);
                            let previous: HashMap<_, _> = state
                                .agents
                                .iter()
                                .map(|a| (a.pane_id.clone(), a.identity()))
                                .collect();
                            let unchanged: HashSet<_> = agents
                                .iter()
                                .filter(|a| previous.get(&a.pane_id) == Some(&a.identity()))
                                .map(|a| a.pane_id.clone())
                                .collect();
                            state.previews.retain(|p, _| unchanged.contains(p));
                            state.errors.retain(|p, _| unchanged.contains(p));
                            state.metrics.retain(|p, _| unchanged.contains(p));
                            state.agents = agents.clone();
                            state.error.clear();
                            state.updated = now();
                            state.revision += 1;
                            g.targets.clone()
                        };
                        notify();
                        for agent in &agents {
                            if worker.0.lock().unwrap().stop {
                                return;
                            }
                            let mut m = telemetry.read(agent);
                            let mut g = worker.0.lock().unwrap();
                            let state = Arc::make_mut(&mut g.state);
                            if m.last_call.is_empty()
                                && let Some(old) = state
                                    .metrics
                                    .get(&agent.pane_id)
                                    .filter(|m| m.call_source == "screen")
                            {
                                m.last_call = old.last_call.clone();
                                m.call_at = old.call_at;
                                m.call_source = "screen".into();
                            }
                            if m.call_source == "transcript" {
                                state.errors.remove(&agent.pane_id);
                            }
                            state.metrics.insert(agent.pane_id.clone(), m);
                            state.revision += 1;
                            drop(g);
                            notify();
                        }
                        let reads: Vec<_> = {
                            let g = worker.0.lock().unwrap();
                            targets
                                .into_iter()
                                .filter(|(id, _)| {
                                    agents.iter().any(|a| a.pane_id == *id)
                                        && g.state
                                            .metrics
                                            .get(id)
                                            .is_none_or(|m| m.call_source != "transcript")
                                })
                                .collect()
                        };
                        // A shared queue gives at most six in-flight reads; each result is published immediately.
                        let read_count = reads.len();
                        let queue = Mutex::new(reads.into_iter());
                        thread::scope(|scope| {
                            for _ in 0..read_count.min(6) {
                                let client = &client;
                                let worker = &worker;
                                let queue = &queue;
                                let notify = &notify;
                                scope.spawn(move || {
                                    loop {
                                        if worker.0.lock().unwrap().stop {
                                            return;
                                        }
                                        let Some((id, lines)) = queue.lock().unwrap().next() else {
                                            return;
                                        };
                                        let result = client.read(&id, lines);
                                        let mut g = worker.0.lock().unwrap();
                                        let state = Arc::make_mut(&mut g.state);
                                        match result {
                                            Ok(text) => {
                                                let text = clean(&text);
                                                state.errors.remove(&id);
                                                if let Some(m) = state
                                                    .metrics
                                                    .get_mut(&id)
                                                    .filter(|m| m.call_source != "transcript")
                                                {
                                                    let name = screen_call(&text);
                                                    if !name.is_empty() {
                                                        if name != m.last_call {
                                                            m.call_at = Some(now());
                                                        }
                                                        m.last_call = name;
                                                        m.call_source = "screen".into();
                                                    }
                                                }
                                                state.previews.insert(id, text);
                                            }
                                            Err(e) => {
                                                state.errors.insert(id, e);
                                            }
                                        }
                                        state.revision += 1;
                                        drop(g);
                                        notify();
                                    }
                                });
                            }
                        });
                    }
                    Err(e) => {
                        let mut g = worker.0.lock().unwrap();
                        let s = Arc::make_mut(&mut g.state);
                        s.error = e;
                        s.revision += 1;
                        drop(g);
                        notify();
                    }
                }
                let guard = worker.0.lock().unwrap();
                let (guard, _) = worker
                    .1
                    .wait_timeout_while(guard, Duration::from_millis(750), |s| !s.wake && !s.stop)
                    .unwrap();
                if guard.stop {
                    return;
                }
            }
        });
        Self { shared }
    }
    pub fn get(&self) -> Arc<State> {
        self.shared.0.lock().unwrap().state.clone()
    }
    pub fn request(&self, targets: HashMap<String, usize>, force: bool) {
        let mut g = self.shared.0.lock().unwrap();
        if g.targets != targets || force {
            g.targets = targets;
            g.wake = true;
            self.shared.1.notify_one();
        }
    }
}
impl Drop for Refresher {
    fn drop(&mut self) {
        let mut g = self.shared.0.lock().unwrap();
        g.stop = true;
        g.wake = true;
        self.shared.1.notify_one();
    }
}
