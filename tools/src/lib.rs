pub mod terminal;
pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub fn root() -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}
pub fn checked(command: &mut std::process::Command) -> Result<std::process::Output> {
    let output = command.output()?;
    if !output.status.success() {
        return Err(format!("{command:?}: {}", String::from_utf8_lossy(&output.stderr)).into());
    }
    Ok(output)
}
