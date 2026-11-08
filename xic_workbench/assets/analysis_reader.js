// Shared RAW reader setup and debounced environment validation.
import { sampleMetadata, samplePages } from "./analysis_samples.js";
import { checklist, dirty } from "./analysis_setup.js";
import {
  state,
  updateConfig,
  updateTargetedSettings,
  updateUntargetedSettings,
} from "./analysis_state.js";
import { $, api, done, el, status } from "./analysis_ui.js";

let readerValidation = 0;
let readerTimer;
function setDllDir(value) {
  readerValidation++;
  clearTimeout(readerTimer);
  if (state.config.settings.dll_dir !== value)
    for (const page of Object.keys(samplePages)) sampleMetadata.clear(page);
  updateTargetedSettings({ dll_dir: value });
  updateUntargetedSettings({ dll_dir: value });
  for (const id of ["t-dll_dir", "u-dll_dir", "dll-dir"]) {
    if ($(id)) $(id).value = value;
  }
}
export function renderReaderSetup(reader) {
  const dotnetMissing = reader.dotnet_errors.length > 0;
  const dllMissing = reader.dll_errors.length > 0;
  $("reader-setup").hidden = !dotnetMissing && !dllMissing;
  $("reader-setup-dotnet").hidden = !dotnetMissing;
  $("reader-setup-dll").hidden = !dllMissing;
  $("reader-setup-existing").hidden = !reader.installed_dir;
  $("reader-setup-use").title = reader.installed_dir;
  $("reader-setup-download").hidden = !!reader.installed_dir;
  $("reader-setup-command").textContent = reader.dotnet_install_command;
  $("reader-setup-version").textContent = reader.version;
  $("reader-setup-license").href = reader.license_url;
  $("reader-setup-detail").replaceChildren(
    ...[...reader.dotnet_errors, ...reader.dll_errors].map((text) =>
      el("li", text),
    ),
  );
  checklist("targeted");
  checklist("untargeted");
}
async function installThermoReader(useInstalled = false) {
  const button = $(useInstalled ? "reader-setup-use" : "reader-setup-install");
  button.disabled = true;
  button.textContent = useInstalled ? "設定中…" : "下載中…";
  try {
    const result = await api(
      `/api/thermo-reader/${useInstalled ? "use-installed" : "install"}`,
      useInstalled ? {} : { accept_license: $("reader-setup-accept").checked },
    );
    setDllDir(result.dll_dir);
    updateConfig({ thermo_reader: result.thermo_reader });
    renderReaderSetup(result.thermo_reader);
    done(result.message);
  } catch (error) {
    status(error.message, true);
  } finally {
    button.textContent = useInstalled
      ? "使用已安裝版本"
      : "下載並設定 RawFileReader";
    button.disabled = !useInstalled && !$("reader-setup-accept").checked;
  }
}

export function bindReader() {
  document.addEventListener("input", (event) => {
    if (!["t-dll_dir", "u-dll_dir", "dll-dir"].includes(event.target.id))
      return;
    setDllDir(event.target.value);
    dirty("targeted");
    dirty("untargeted");
    const version = readerValidation;
    const dllDir = event.target.value.trim();
    readerTimer = setTimeout(async () => {
      try {
        const reader = await api("/api/thermo-reader/status", {
          dll_dir: dllDir,
        });
        if (version !== readerValidation) return;
        updateConfig({ thermo_reader: reader });
        renderReaderSetup(reader);
      } catch (error) {
        if (version === readerValidation) status(error.message, true);
      }
    }, 250);
  });
  $("reader-setup-accept").addEventListener("change", (event) => {
    $("reader-setup-install").disabled = !event.target.checked;
  });
  $("reader-setup-install").addEventListener("click", () =>
    installThermoReader(),
  );
  $("reader-setup-use").addEventListener("click", () =>
    installThermoReader(true),
  );
  $("reader-setup-copy").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("reader-setup-command").textContent);
    done("已複製安裝指令。");
  });
}
