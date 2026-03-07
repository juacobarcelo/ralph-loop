#!/usr/bin/env node

const fs = require("fs")
const http = require("http")
const path = require("path")
const { spawn } = require("child_process")

const DEBUG_PORT = Number.parseInt(process.env.CHROME_MCP_DEBUG_PORT || "9222", 10)
const BROWSER_URL = `http://127.0.0.1:${DEBUG_PORT}`
const USER_DATA_DIR = process.env.CHROME_MCP_USER_DATA_DIR || "/tmp/chrome-mcp-profile"
const BROWSER_TIMEOUT_MS = Number.parseInt(
  process.env.CHROME_MCP_BROWSER_TIMEOUT_MS || "20000",
  10
)

let browserProcess = null
let mcpProcess = null
let shuttingDown = false

function log(message) {
  process.stderr.write(`[chrome-mcp-proxy] ${message}\n`)
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function isExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK)
    return true
  } catch {
    return false
  }
}

function chromiumCandidates() {
  const candidates = []
  const preferred = process.env.CHROME_MCP_CHROMIUM_PATH
  if (preferred) {
    candidates.push(preferred)
  }

  candidates.push("/usr/bin/chromium", "/usr/bin/chromium-browser")

  const playwrightRoot = "/ms-playwright"
  if (fs.existsSync(playwrightRoot)) {
    const entries = fs.readdirSync(playwrightRoot)
    const chromiumDirs = entries
      .filter(entry => entry.startsWith("chromium-") && !entry.startsWith("chromium_headless_shell-"))
      .sort()
      .reverse()

    for (const entry of chromiumDirs) {
      candidates.push(
        path.join(playwrightRoot, entry, "chrome-linux64", "chrome"),
        path.join(playwrightRoot, entry, "chrome-linux", "chrome")
      )
    }

    const headlessShellDirs = entries
      .filter(entry => entry.startsWith("chromium_headless_shell-"))
      .sort()
      .reverse()

    for (const entry of headlessShellDirs) {
      candidates.push(
        path.join(playwrightRoot, entry, "chrome-headless-shell-linux64", "chrome-headless-shell")
      )
    }
  }

  return candidates
}

function findChromiumBinary() {
  for (const candidate of chromiumCandidates()) {
    if (isExecutable(candidate)) {
      return candidate
    }
  }

  throw new Error("No Chromium executable found for Chrome DevTools MCP proxy")
}

function browserReady() {
  return new Promise(resolve => {
    const request = http.get(`${BROWSER_URL}/json/version`, response => {
      response.resume()
      resolve(response.statusCode === 200)
    })

    request.setTimeout(1000, () => {
      request.destroy()
      resolve(false)
    })

    request.on("error", () => resolve(false))
  })
}

async function waitForBrowser() {
  const deadline = Date.now() + BROWSER_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (await browserReady()) {
      return
    }
    await sleep(500)
  }

  throw new Error(`Chromium did not become ready on ${BROWSER_URL} within ${BROWSER_TIMEOUT_MS}ms`)
}

function launchChromium() {
  if (browserProcess) {
    return
  }

  const chromiumPath = findChromiumBinary()
  fs.mkdirSync(USER_DATA_DIR, { recursive: true })

  const args = [
    `--remote-debugging-port=${DEBUG_PORT}`,
    "--remote-debugging-address=127.0.0.1",
    `--user-data-dir=${USER_DATA_DIR}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-sandbox",
    "--headless=new",
    "about:blank",
  ]

  log(`launching Chromium from ${chromiumPath}`)
  browserProcess = spawn(chromiumPath, args, {
    stdio: ["ignore", "ignore", "pipe"],
  })

  browserProcess.stderr.on("data", chunk => {
    process.stderr.write(chunk)
  })

  browserProcess.on("exit", code => {
    browserProcess = null
    if (!shuttingDown) {
      log(`Chromium exited unexpectedly with code ${code ?? "unknown"}`)
    }
  })
}

function startMcp() {
  if (mcpProcess) {
    return
  }

  log("starting chrome-devtools-mcp")
  mcpProcess = spawn("chrome-devtools-mcp", [`--browser-url=${BROWSER_URL}`], {
    stdio: ["pipe", "pipe", "pipe"],
  })

  process.stdin.pipe(mcpProcess.stdin)
  process.stdin.on("end", () => {
    if (mcpProcess && mcpProcess.stdin.writable) {
      mcpProcess.stdin.end()
    }
  })

  mcpProcess.stdout.on("data", chunk => {
    process.stdout.write(chunk)
  })

  mcpProcess.stderr.on("data", chunk => {
    process.stderr.write(chunk)
  })

  mcpProcess.on("exit", code => {
    process.exit(code ?? 0)
  })
}

function shutdown() {
  shuttingDown = true
  if (mcpProcess) {
    mcpProcess.kill("SIGTERM")
  }
  if (browserProcess) {
    browserProcess.kill("SIGTERM")
  }
}

process.on("SIGINT", shutdown)
process.on("SIGTERM", shutdown)

async function main() {
  launchChromium()
  await waitForBrowser()
  startMcp()
}

main().catch(error => {
  log(error instanceof Error ? error.message : String(error))
  shutdown()
  process.exit(1)
})
