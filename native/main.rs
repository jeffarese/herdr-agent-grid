use std::io::{IsTerminal, Write};
fn main() {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let diagnostic = args.iter().any(|a| {
        matches!(
            a.as_str(),
            "--doctor" | "--list" | "--render" | "--version" | "--help" | "-h" | "install"
        )
    });
    if let Err(e) = herdr_agent_grid::cli::main(args) {
        eprintln!("herdr-agent-grid: {e}");
        if !diagnostic && std::io::stdin().is_terminal() && std::io::stdout().is_terminal() {
            print!("\nAgent Grid could not start. Press Enter to close. ");
            let _ = std::io::stdout().flush();
            let mut line = String::new();
            let _ = std::io::stdin().read_line(&mut line);
        }
        std::process::exit(
            if e.starts_with("run from a Herdr pane") || e.starts_with("Needs a terminal") {
                2
            } else {
                1
            },
        );
    }
}
