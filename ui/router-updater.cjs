"use strict";

// Serves the desktop's own update UI (the rail's "Update available" button,
// Check for Updates, the install confirmation) from the router's updater,
// scripts/update.py, instead of Sparkle. The updater builds and launch-checks
// the next release in the background; this module shows it as a ready update
// and, on install or on quit with automatic updates on, hands the updater the
// swap once the app has exited.

const { execFile, spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, dialog } = require("electron");

const ROOT = path.join(os.homedir(), ".codex-mux", "update");
const LABEL = "app.cdxmux.updater";
const BUSY = new Set(["checking", "building"]);

function readJson(name) {
  try {
    return JSON.parse(fs.readFileSync(path.join(ROOT, name), "utf8"));
  } catch {
    return null;
  }
}

function runUpdater(args) {
  const settings = readJson("settings.json");
  if (!settings?.python) return false;
  spawn(settings.python, [path.join(ROOT, "update.py"), ...args], {
    detached: true,
    stdio: "ignore",
  }).unref();
  return true;
}

function startCheck() {
  return new Promise((resolve) => {
    execFile(
      "/bin/launchctl",
      ["kickstart", `gui/${process.getuid()}/${LABEL}`],
      (error) => resolve(error == null),
    );
  });
}

async function waitForCheck(startedAt) {
  const deadline = Date.now() + 45 * 60_000;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    const state = readJson("state.json");
    if (state && Date.parse(state.updatedAt) >= startedAt && !BUSY.has(state.status)) {
      return state;
    }
  }
  return null;
}

exports.attach = async (manager) => {
  await manager.initialize();
  manager.inAppUpdatesLaunchPolicy = "allowed";
  manager.inAppUpdatesLaunchPolicyResolution.resolve(undefined);
  manager.latchInAppUpdatesEnabledForLaunch = async () => {};
  manager.hasInAppUpdatesPolicyChanges = () => false;

  let checking = false;
  let installing = false;
  const setLifecycle = (state) => {
    manager.updateLifecycleState = state;
    manager.setUpdateLifecycleState(state);
  };
  const sync = () => {
    if (checking || installing) return;
    const ready = readJson("state.json")?.status === "ready";
    manager.setUpdateReady(ready);
    setLifecycle(ready ? "ready" : "idle");
  };

  manager.updater = {
    async checkForUpdates() {
      if (checking) return;
      const startedAt = Date.now();
      if (!readJson("settings.json") || !(await startCheck())) {
        await dialog.showMessageBox({
          type: "info",
          message: "Updates are not set up",
          detail: "Run `python3 scripts/update.py enable` in the router repository.",
        });
        return;
      }
      checking = true;
      setLifecycle("checking");
      const state = await waitForCheck(startedAt);
      checking = false;
      sync();
      if (state?.status === "up-to-date") {
        await dialog.showMessageBox({
          type: "info",
          message: `${app.getName()} is up to date`,
          detail: state.installed ? `Installed: ${state.installed}` : undefined,
        });
      } else if (state?.status === "failed") {
        await dialog.showMessageBox({
          type: "warning",
          message: "The update could not be prepared",
          detail: state.message,
        });
      }
    },
    async checkForUpdateInformation() {
      const state = readJson("state.json");
      return state?.status === "ready"
        ? { status: "restart_required" }
        : { status: "unavailable", reason: state?.message || "No router update is ready." };
    },
    async installUpdatesIfAvailable() {
      if (readJson("state.json")?.status !== "ready") return false;
      if (!runUpdater(["apply", "--relaunch", "--after-pid", String(process.pid)])) return false;
      installing = true;
      setLifecycle("installing");
      app.quit();
      return true;
    },
  };

  app.on("will-quit", () => {
    if (installing || !readJson("settings.json")?.auto) return;
    if (readJson("state.json")?.status !== "ready") return;
    installing = runUpdater(["apply", "--after-pid", String(process.pid)]);
  });
  sync();
  setInterval(sync, 60_000).unref();
};
