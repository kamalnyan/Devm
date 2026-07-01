#!/usr/bin/env bash
# =============================================================================
# DevManager — One-command installer
# =============================================================================
# Usage:
#   ./install.sh                         # full setup (recommended)
#   ./install.sh --profile minimal       # minimal: no Ollama, rules-only
#   ./install.sh --profile developer     # default
#   ./install.sh --profile security      # security-focused
#   ./install.sh --model llama3.2        # use smaller 2GB model
#   ./install.sh --no-adk                # skip Google ADK
#   ./install.sh --skip-ollama           # assume Ollama already installed
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
DEVM_BIN="/usr/local/bin/devm"
MIN_PYTHON="3.11"

# Defaults
PROFILE="developer"
MODEL="glm4"
INSTALL_ADK=true
SKIP_OLLAMA=false
SKIP_MODEL_PULL=false
PROVIDER=""      # set interactively or via --provider flag
API_KEY=""

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[devmanager]${RESET} $*"; }
ok()   { echo -e "${GREEN}  ✓${RESET} $*"; }
warn() { echo -e "${YELLOW}  ⚠${RESET} $*"; }
fail() { echo -e "${RED}  ✗ ERROR:${RESET} $*"; exit 1; }
step() { echo -e "\n${BOLD}━━ $* ${RESET}"; }

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)    PROFILE="$2"; shift 2 ;;
    --model)      MODEL="$2"; shift 2 ;;
    --provider)   PROVIDER="$2"; shift 2 ;;
    --api-key)    API_KEY="$2"; shift 2 ;;
    --no-adk)     INSTALL_ADK=false; shift ;;
    --skip-ollama) SKIP_OLLAMA=true; shift ;;
    --skip-model) SKIP_MODEL_PULL=true; shift ;;
    --help|-h)
      echo "Usage: ./install.sh [--profile minimal|developer|security|research|full]"
      echo "                    [--provider ollama|openai|anthropic|gemini|groq|together]"
      echo "                    [--model MODEL_NAME]  [--api-key YOUR_KEY]"
      echo "                    [--no-adk] [--skip-ollama]"
      exit 0 ;;
    *) warn "Unknown flag: $1"; shift ;;
  esac
done

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║        DevManager — Local AI Dev Router          ║${RESET}"
echo -e "${BOLD}║  Google ADK + Ollama GLM, no paid API keys       ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
echo -e "  Profile : ${CYAN}$PROFILE${RESET}"
echo -e "  Model   : ${CYAN}$MODEL${RESET}"
echo -e "  ADK     : ${CYAN}$INSTALL_ADK${RESET}"
echo ""

# =============================================================================
# STEP 1 — Python
# =============================================================================
step "Step 1/6 — Python $MIN_PYTHON+"

PYTHON=""
for cmd in python3.12 python3.11 python3; do
  if command -v "$cmd" &>/dev/null; then
    ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    major="${ver%%.*}"; minor="${ver##*.}"
    if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
      PYTHON="$cmd"; ok "Found $cmd ($ver)"; break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  log "Python 3.11+ not found — trying to install..."
  if [[ "$(uname)" == "Darwin" ]]; then
    if ! command -v brew &>/dev/null; then
      log "Installing Homebrew..."
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    brew install python@3.12
    PYTHON="$(brew --prefix)/bin/python3.12"
    ok "Installed Python 3.12 via Homebrew"
  elif command -v apt-get &>/dev/null; then
    sudo apt-get update -qq && sudo apt-get install -y python3.11 python3.11-venv python3-pip
    PYTHON="python3.11"
    ok "Installed Python 3.11 via apt"
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3.11 python3.11-pip
    PYTHON="python3.11"
    ok "Installed Python 3.11 via dnf"
  else
    fail "Python 3.11+ required. Install from https://python.org and re-run."
  fi
fi

# =============================================================================
# STEP 1b — AI Provider selection (interactive if not set via flag)
# =============================================================================

if [[ -z "$PROVIDER" ]]; then
  echo ""
  echo -e "${BOLD}Choose your AI provider:${RESET}"
  echo -e "  ${CYAN}1${RESET}) Ollama (local, free, private — recommended for most users)"
  echo -e "  ${CYAN}2${RESET}) OpenAI  (GPT-4o / GPT-4o-mini — paid, needs API key)"
  echo -e "  ${CYAN}3${RESET}) Anthropic Claude (claude-3-5-haiku — paid, needs API key)"
  echo -e "  ${CYAN}4${RESET}) Google Gemini (gemini-1.5-flash — free tier available)"
  echo -e "  ${CYAN}5${RESET}) Groq (llama3 — free tier, very fast)"
  echo -e "  ${CYAN}6${RESET}) Together AI (open models — paid)"
  echo -e "  ${CYAN}7${RESET}) OpenAI-compatible (LM Studio, vLLM, custom endpoint)"
  echo ""
  read -r -p "  Enter choice [1-7, default=1]: " PROVIDER_CHOICE
  case "${PROVIDER_CHOICE:-1}" in
    1) PROVIDER="ollama" ;;
    2) PROVIDER="openai";     MODEL="gpt-4o-mini" ;;
    3) PROVIDER="anthropic";  MODEL="claude-3-5-haiku-20241022" ;;
    4) PROVIDER="gemini";     MODEL="gemini-1.5-flash" ;;
    5) PROVIDER="groq";       MODEL="llama-3.1-8b-instant" ;;
    6) PROVIDER="together";   MODEL="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo" ;;
    7) PROVIDER="openai-compatible"; MODEL="local-model" ;;
    *) PROVIDER="ollama" ;;
  esac
  ok "Provider: $PROVIDER / Model: $MODEL"

  # Ask for API key if needed
  if [[ "$PROVIDER" != "ollama" && "$PROVIDER" != "openai-compatible" ]]; then
    declare -A ENV_KEY_MAP=(
      [openai]="OPENAI_API_KEY"
      [anthropic]="ANTHROPIC_API_KEY"
      [gemini]="GOOGLE_API_KEY"
      [groq]="GROQ_API_KEY"
      [together]="TOGETHER_API_KEY"
    )
    ENV_KEY="${ENV_KEY_MAP[$PROVIDER]:-API_KEY}"
    if [[ -z "$API_KEY" && -n "${!ENV_KEY:-}" ]]; then
      API_KEY="${!ENV_KEY}"
      ok "API key found in environment ($ENV_KEY)"
    elif [[ -z "$API_KEY" ]]; then
      echo ""
      read -r -s -p "  Enter $ENV_KEY (or press Enter to set later): " API_KEY
      echo ""
    fi
  fi

  if [[ "$PROVIDER" == "openai-compatible" ]]; then
    read -r -p "  Enter base URL [http://localhost:1234/v1]: " CUSTOM_URL
    CUSTOM_URL="${CUSTOM_URL:-http://localhost:1234/v1}"
  fi
fi

# Non-Ollama providers don't need local Ollama
if [[ "$PROVIDER" != "ollama" ]]; then
  SKIP_OLLAMA=true
  SKIP_MODEL_PULL=true
fi

# =============================================================================
# STEP 2 — Ollama
# =============================================================================
step "Step 2/6 — Ollama (local LLM server)"

if [[ "$PROFILE" == "minimal" ]]; then
  warn "Profile=minimal — skipping Ollama (rules-only mode, no LLM)"
  SKIP_OLLAMA=true
fi

if [[ "$SKIP_OLLAMA" == "false" ]]; then
  if ! command -v ollama &>/dev/null; then
    log "Ollama not found — installing..."
    if [[ "$(uname)" == "Darwin" ]]; then
      if command -v brew &>/dev/null; then
        brew install ollama
        ok "Ollama installed via Homebrew"
      else
        curl -fsSL https://ollama.com/install.sh | sh
        ok "Ollama installed via install script"
      fi
    else
      curl -fsSL https://ollama.com/install.sh | sh
      ok "Ollama installed"
    fi
  else
    ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
  fi

  # Start Ollama server if not running
  if ! curl -sf http://127.0.0.1:11434/api/version &>/dev/null; then
    log "Starting Ollama server in background..."
    ollama serve &>/dev/null &
    OLLAMA_PID=$!
    sleep 3
    if curl -sf http://127.0.0.1:11434/api/version &>/dev/null; then
      ok "Ollama server started (pid=$OLLAMA_PID)"
    else
      warn "Ollama server may not be running. Start manually: ollama serve"
    fi
  else
    ok "Ollama server already running"
  fi
else
  warn "Skipping Ollama setup"
fi

# =============================================================================
# STEP 3 — Pull GLM model
# =============================================================================
step "Step 3/6 — GLM model ($MODEL)"

if [[ "$SKIP_OLLAMA" == "false" && "$SKIP_MODEL_PULL" == "false" ]]; then
  MODEL_TAG="${MODEL}:latest"
  if ollama list 2>/dev/null | grep -q "^${MODEL}"; then
    ok "Model already available: $MODEL"
  else
    log "Pulling $MODEL_TAG — this may take a few minutes..."
    log "Model size reference: glm4≈5.5GB  llama3.2≈2GB  mistral≈4GB  qwen2.5-coder≈4.7GB"
    ollama pull "$MODEL_TAG"
    ok "Model ready: $MODEL_TAG"
  fi
else
  warn "Skipping model pull"
fi

# =============================================================================
# STEP 4 — Python virtualenv + pip install
# =============================================================================
step "Step 4/6 — Python environment"

if [[ ! -d "$VENV_DIR" ]]; then
  log "Creating virtualenv at $VENV_DIR ..."
  "$PYTHON" -m venv "$VENV_DIR"
  ok "Virtualenv created"
else
  ok "Virtualenv exists: $VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
PYTHON_VENV="$VENV_DIR/bin/python"

log "Installing devmanager (core)..."
"$PIP" install -q --upgrade pip
"$PIP" install -q -e "$REPO_DIR"
ok "devmanager installed"

log "Installing Google ADK + LiteLLM..."
"$PIP" install -q "google-adk>=0.5.0" "litellm>=1.40.0" || {
  warn "ADK install had issues. Run manually: pip install google-adk litellm"
}
ok "google-adk + litellm installed"

# =============================================================================
# STEP 5 — devm command (global)
# =============================================================================
step "Step 5/6 — devm command in PATH"

DEVM_WRAPPER="$REPO_DIR/scripts/devm-wrapper.sh"
cat > "$DEVM_WRAPPER" << WRAPPER
#!/usr/bin/env bash
exec "$PYTHON_VENV" -m devmanager "\$@"
WRAPPER
chmod +x "$DEVM_WRAPPER"

# Try /usr/local/bin (needs sudo on some systems), fallback to ~/.local/bin
install_devm_link() {
  local target="$1"
  mkdir -p "$(dirname "$target")"
  ln -sf "$DEVM_WRAPPER" "$target" && ok "devm → $target" && return 0
  return 1
}

DEVM_INSTALLED=false
if install_devm_link "/usr/local/bin/devm" 2>/dev/null; then
  DEVM_INSTALLED=true
elif sudo ln -sf "$DEVM_WRAPPER" "/usr/local/bin/devm" 2>/dev/null; then
  ok "devm → /usr/local/bin/devm (sudo)"
  DEVM_INSTALLED=true
else
  LOCAL_BIN="$HOME/.local/bin"
  mkdir -p "$LOCAL_BIN"
  ln -sf "$DEVM_WRAPPER" "$LOCAL_BIN/devm"
  ok "devm → $LOCAL_BIN/devm"
  DEVM_INSTALLED=true
  # Ensure ~/.local/bin is in PATH
  SHELL_RC=""
  if [[ -f "$HOME/.zshrc" ]]; then SHELL_RC="$HOME/.zshrc"
  elif [[ -f "$HOME/.bashrc" ]]; then SHELL_RC="$HOME/.bashrc"; fi
  if [[ -n "$SHELL_RC" ]] && ! grep -q '\.local/bin' "$SHELL_RC"; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    warn "Added ~/.local/bin to PATH in $SHELL_RC — reload shell: source $SHELL_RC"
  fi
fi

# Also update the existing install-devm.sh to use new venv
cat > "$REPO_DIR/scripts/install-devm.sh" << DEVM
#!/usr/bin/env bash
# Re-run this if devm disappears from PATH
ln -sf "$DEVM_WRAPPER" /usr/local/bin/devm && echo "devm installed" || \
  { mkdir -p "\$HOME/.local/bin" && ln -sf "$DEVM_WRAPPER" "\$HOME/.local/bin/devm" && echo "devm installed to ~/.local/bin"; }
DEVM
chmod +x "$REPO_DIR/scripts/install-devm.sh"

# =============================================================================
# STEP 6 — Verify
# =============================================================================
step "Step 6/6 — Verification"

# Save provider config
log "Saving provider config..."
CONFIG_JSON="{\"provider\": \"$PROVIDER\", \"model\": \"$MODEL\""
if [[ -n "$API_KEY" ]]; then
  CONFIG_JSON+=", \"api_key\": \"$API_KEY\""
fi
if [[ -n "${CUSTOM_URL:-}" ]]; then
  CONFIG_JSON+=", \"base_url\": \"$CUSTOM_URL\""
fi
CONFIG_JSON+="}"
mkdir -p "$HOME/.devmanager"
echo "$CONFIG_JSON" > "$HOME/.devmanager/config.json"
ok "Config saved to ~/.devmanager/config.json"

if "$PYTHON_VENV" -m devmanager --doctor 2>/dev/null | grep -q "Status: OK"; then
  ok "Doctor check passed"
else
  "$PYTHON_VENV" -m devmanager --doctor || true
fi

# =============================================================================
# Done
# =============================================================================
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}${BOLD}  Devm installed! ⚡${RESET}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "  ${BOLD}Modes:${RESET}"
echo -e "    ${CYAN}devm \"task\"${RESET}                  auto mode  (council, no prompts)"
echo -e "    ${CYAN}devm --mode plan \"task\"${RESET}      plan only  (analyze, no changes)"
echo -e "    ${CYAN}devm --mode ask  \"task\"${RESET}      ask mode   (ask before each file change)"
echo -e "    ${CYAN}devm --mode turbo \"task\"${RESET}     turbo mode (fully autonomous)"
echo ""
echo -e "  ${BOLD}Agents:${RESET}"
echo -e "    ${CYAN}devm agents${RESET}                  see what's installed"
echo -e "    ${CYAN}devm agent-add${RESET}               add a new agent"
echo -e "    ${CYAN}devm --doctor${RESET}                health check"
echo ""
echo -e "  ${BOLD}Provider:${RESET}  $PROVIDER  |  ${BOLD}Model:${RESET}  $MODEL"
echo ""
if [[ "$PROVIDER" != "ollama" ]]; then
  echo -e "  ${YELLOW}Set your API key:${RESET}  devm config set api_key=YOUR_KEY"
fi
echo -e "  ${BOLD}Switch provider:${RESET}  devm config set provider=groq model=llama-3.1-8b-instant"
echo ""

# Auto-discover installed agents
log "Discovering installed agents..."
"$PYTHON_VENV" -m devmanager agents 2>/dev/null || true
echo ""
