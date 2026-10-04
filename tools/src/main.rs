mod benchmark;
mod media;
mod package;
use grid_tools::Result;
fn run() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(root) = std::env::var_os("GRID_FIXTURE_ROOT") {
        return mock_herdr(std::path::Path::new(&root), &args);
    }
    match args.first().map(String::as_str) {
        Some("package") => package::run(&args[1..]),
        Some("bench") => benchmark::run(&args[1..]),
        Some("media") => media::run(&args[1..]),
        None | Some("--help" | "-h") => {
            println!(
                "Development tasks\n\n  cargo xtask package [--target TRIPLE] [--binary PATH] [--output DIR]\n  cargo xtask bench [--binary PATH] [--rounds N] [--output FILE]\n  cargo xtask media [--output DIR] [--stills-only]\n\nBuild the application with cargo build --release --locked --bin herdr-agent-grid.\nRun benchmarks with cargo run --release --locked -p grid-tools --bin xtask -- bench.\nMedia animation encoding requires ffmpeg."
            );
            Ok(())
        }
        Some(arg) => Err(format!("Unknown task: {arg}").into()),
    }
}
fn mock_herdr(root: &std::path::Path, args: &[String]) -> Result<()> {
    use std::io::Write;
    let mut log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(root.join("calls.jsonl"))?;
    // Append each complete record together so concurrent mock processes cannot
    // insert another record between the JSON payload and its newline.
    let mut record = serde_json::to_vec(args)?;
    record.push(b'\n');
    log.write_all(&record)?;
    let result = match args.first().map(String::as_str) {
        Some("api") => {
            serde_json::json!({"snapshot":serde_json::from_slice::<serde_json::Value>(&std::fs::read(root.join("snapshot.json"))?)?})
        }
        Some("pane") => {
            if args.get(2).is_some_and(|a| a == "w1:p1") && root.join("slow").exists() {
                std::thread::sleep(std::time::Duration::from_millis(1500));
                std::fs::write(root.join("slow-finished"), "")?;
            }
            serde_json::json!({"read":{"text":"● Bash(synthetic check)\nCURRENT PROMPT"}})
        }
        _ => serde_json::json!({"ok":true}),
    };
    println!("{}", serde_json::json!({"result":result}));
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1)
    }
}
