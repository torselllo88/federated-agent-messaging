# Windows convenience shim.
#
# GNU make is not present on a default Windows host. The Makefile remains
# authoritative and is what the designated Linux formal-run host uses
# (testbed-architecture.md §32, experimental-protocol.md §39); this script only
# mirrors the same targets so development on Windows is not blocked.
#
#   .\make.ps1 setup
#   .\make.ps1 verify
#   .\make.ps1 e0
#   .\make.ps1 analyse

param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'build', 'up', 'setup', 'verify', 'spike', 'e0', 'e1', 'e2-pilot', 'e2', 'e3-readiness', 'e3-pilot', 'e3', 'e4-prepare', 'e4-ca', 'e4', 'e4-validate', 'inventory', 'analyse', 'test', 'down', 'clean', 'logs')]
    [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'

function Require-ResultsDir {
    if (-not $env:FAM_RESULTS_DIR) {
        Write-Error @'
FAM_RESULTS_DIR is not set.

  $env:FAM_RESULTS_DIR = "C:\path\outside\this\repository"

Run-generated artifacts are written outside the tracked worktree for the whole
campaign (experimental-protocol.md §37).
'@
    }
    New-Item -ItemType Directory -Force -Path $env:FAM_RESULTS_DIR | Out-Null
}

function Invoke-Compose {
    param([string[]]$Arguments)
    & docker compose --profile tools @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not $env:FAM_PROTOCOL_GIT_COMMIT) {
    try { $env:FAM_PROTOCOL_GIT_COMMIT = (git rev-parse HEAD).Trim() }
    catch { $env:FAM_PROTOCOL_GIT_COMMIT = 'unknown' }
}

switch ($Target) {
    'help' {
        Write-Output @'
.\make.ps1 setup    - build, generate TLS and configs, start both domains, provision accounts
.\make.ps1 up       - start both domains without re-provisioning (after `down`)
.\make.ps1 verify   - environment and federation transport/bootstrap readiness
.\make.ps1 spike    - development compatibility spike (Synapse / nio / room v12)
.\make.ps1 e0       - run the frozen E0 procedure (3 independent runs)
.\make.ps1 e1       - run the frozen E1 procedure (3 independent federated runs)
.\make.ps1 e2-pilot - development pilot: select the E2 sync timeline limit
.\make.ps1 e2       - run the frozen E2 procedure (3 independent recovery runs)
.\make.ps1 e3-readiness - live gap recovery under bounded-concurrency stress
.\make.ps1 e3-pilot - development E3 pilot: benchmark mechanics and sync limit
.\make.ps1 e3       - the development E3 campaign (120 paired benchmark runs)
.\make.ps1 e4-prepare - check E4 readiness and print human-client details
.\make.ps1 e4-ca    - print the research CA for the human client trust store
.\make.ps1 e4       - run ONE human-driven E4 session (interactive)
.\make.ps1 e4-validate - validate the recorded E4 sessions
.\make.ps1 inventory - testbed configuration inventory for Task 07
.\make.ps1 analyse  - digest verification, schema validation, E0 summary
.\make.ps1 test     - unit tests
.\make.ps1 down     - stop containers
.\make.ps1 clean    - stop containers and delete all volumes (destructive)
'@
    }
    'build' {
        Require-ResultsDir
        Invoke-Compose @('build')
    }
    'setup' {
        Require-ResultsDir
        Invoke-Compose @('build')
        Invoke-Compose @('run', '--rm', '--no-deps', 'bootstrap', 'python', 'scripts/bootstrap.py', 'tls')
        Invoke-Compose @('run', '--rm', '--no-deps', 'bootstrap', 'python', 'scripts/bootstrap.py', 'config')
        Invoke-Compose @('up', '-d', 'postgres-a', 'postgres-b', 'synapse-a', 'synapse-b')
        Invoke-Compose @('run', '--rm', 'bootstrap', 'python', 'scripts/bootstrap.py', 'wait')
        Invoke-Compose @('run', '--rm', 'bootstrap', 'python', 'scripts/bootstrap.py', 'provision')
        Invoke-Compose @('run', '--rm', 'bootstrap', 'python', 'scripts/collect_environment.py')
        Write-Output "`nsetup complete. next: .\make.ps1 verify"
    }
    'up'      {
        # Restart the homeservers on the existing volumes. Deliberately does
        # not re-provision: accounts, TLS and signing keys already exist, and
        # re-running provisioning would reissue the credentials the human
        # participant is already using.
        Require-ResultsDir
        Invoke-Compose @('up', '-d', 'postgres-a', 'postgres-b', 'synapse-a', 'synapse-b')
        Invoke-Compose @('run', '--rm', 'bootstrap', 'python', 'scripts/bootstrap.py', 'wait')
    }
    'verify'  { Require-ResultsDir; Invoke-Compose @('run', '--rm', 'bootstrap', 'python', 'scripts/verify_environment.py') }
    'spike'   { Require-ResultsDir; Invoke-Compose @('run', '--rm', 'toolbox', 'python', 'scripts/spike_compatibility.py') }
    'e0'      { Require-ResultsDir; Invoke-Compose @('run', '--rm', 'toolbox', 'python', 'experiments/e0_baseline.py') }
    'e1'      { Require-ResultsDir; Invoke-Compose @('run', '--rm', 'toolbox', 'python', 'experiments/e1_federation.py') }
    'e2-pilot'{ Require-ResultsDir; Invoke-Compose @('run', '--rm', 'toolbox', 'python', 'scripts/e2_pilot.py') }
    'e2'      { Require-ResultsDir; Invoke-Compose @('run', '--rm', '-e', 'FAM_E2_TIMELINE_LIMIT', 'toolbox', 'python', 'experiments/e2_recovery.py') }
    'e3-readiness' { Require-ResultsDir; Invoke-Compose @('run', '--rm', '-e', 'FAM_READINESS_REQUESTS', '-e', 'FAM_READINESS_CONCURRENCY', '-e', 'FAM_READINESS_TIMELINE_LIMIT', 'toolbox', 'python', 'experiments/e3_readiness.py') }
    'e3-pilot' { Require-ResultsDir; Invoke-Compose @('run', '--rm', '-e', 'FAM_E3_TIMELINE_LIMIT', '-e', 'FAM_E3_SYNC_TIMEOUT_MS', '-e', 'FAM_E3_PILOT_LATENCY_WARMUP', '-e', 'FAM_E3_PILOT_LATENCY_MEASURED', '-e', 'FAM_E3_PILOT_WARMUP_S', '-e', 'FAM_E3_PILOT_MEASUREMENT_S', '-e', 'FAM_E3_PILOT_DRAIN_S', 'toolbox', 'python', 'scripts/e3_pilot.py') }
    'e3'      { Require-ResultsDir; Invoke-Compose @('run', '--rm', '-e', 'FAM_E3_SCHEDULE_SEED', '-e', 'FAM_E3_TIMELINE_LIMIT', '-e', 'FAM_E3_SYNC_TIMEOUT_MS', '-e', 'FAM_E3_BLOCKS', '-e', 'FAM_E3_WORKLOADS', 'toolbox', 'python', 'experiments/e3_benchmark.py') }
    'e4-prepare' { Require-ResultsDir; Invoke-Compose @('run', '--rm', '-e', 'FAM_LLM_PROVIDER', '-e', 'FAM_LLM_MODEL', '-e', 'FAM_LLM_API_KEY', '-e', 'FAM_LLM_BASE_URL', '-e', 'FAM_E4_CS_TLS_PORT', 'bootstrap', 'python', 'scripts/e4_prepare.py') }
    'e4-ca'   { Invoke-Compose @('run', '--rm', '--no-deps', '-T', 'bootstrap', 'cat', '/tls/ca.crt') }
    'e4'      { Require-ResultsDir; Invoke-Compose @('run', '--rm', '-e', 'FAM_LLM_PROVIDER', '-e', 'FAM_LLM_MODEL', '-e', 'FAM_LLM_API_KEY', '-e', 'FAM_LLM_BASE_URL', '-e', 'FAM_LLM_MAX_TOKENS', '-e', 'FAM_LLM_SYSTEM_PROMPT', '-e', 'FAM_E4_SESSION_ID', '-e', 'FAM_E4_CLIENT_NAME', '-e', 'FAM_E4_CLIENT_VERSION', '-e', 'FAM_E4_CLIENT_HOST', '-e', 'FAM_E4_JOIN_TIMEOUT', '-e', 'FAM_E4_TIMEOUT', '-e', 'FAM_E4_CONFIRM_VISIBLE', 'toolbox', 'python', 'experiments/e4_human_llm.py') }
    'e4-validate' { Require-ResultsDir; Invoke-Compose @('run', '--rm', '--no-deps', 'toolbox', 'python', 'scripts/e4_validate.py') }
    'inventory' { Require-ResultsDir; Invoke-Compose @('run', '--rm', 'bootstrap', 'python', 'scripts/testbed_inventory.py') }
    'analyse' { Require-ResultsDir; Invoke-Compose @('run', '--rm', '--no-deps', 'toolbox', 'python', 'scripts/analyse.py') }
    'test'    {
        Require-ResultsDir
        Invoke-Compose @('build')
        Invoke-Compose @('run', '--rm', '--no-deps', '-e', 'FAM_RESULTS_DIR=/tmp/fam-test-results', 'toolbox', 'python', '-m', 'pytest', 'tests', '-q')
    }
    'down'    { Invoke-Compose @('down') }
    'clean'   { Invoke-Compose @('down', '-v') }
    'logs'    { Invoke-Compose @('logs', '--tail=200', 'synapse-a', 'synapse-b') }
}
