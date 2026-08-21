(() => {
  "use strict";

  const list = document.getElementById("fileList");
  const dataNode = document.getElementById("uploadedFilesData");
  if (!list || !dataNode) return;

  let files = [];
  try {
    files = JSON.parse(dataNode.textContent || "[]");
  } catch (_) {
    files = [];
  }

  function formatBytes(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = Number(bytes) || 0;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 2)} ${units[unit]}`;
  }

  if (!Array.isArray(files) || files.length === 0) {
    const item = document.createElement("li");
    item.className = "list-group-item text-danger";
    item.textContent = "No files to display.";
    list.appendChild(item);
  } else {
    files.forEach((file) => renderFile(file));
  }

  function renderFile(file) {
    const item = document.createElement("li");
    item.className = "list-group-item";

    const row = document.createElement("div");
    row.className = "d-flex align-items-center w-100 file-row";

    const name = document.createElement("span");
    name.className = "filename-text";
    name.textContent = file.name;
    name.title = file.name;

    const size = document.createElement("span");
    size.className = "file-size";
    size.textContent = `(${formatBytes(file.size)})`;

    const actions = document.createElement("div");
    actions.className = "d-flex gap-2 file-actions";

    if (file.preview_kind) {
      const preview = document.createElement("button");
      preview.type = "button";
      preview.className = "btn btn-sm btn-outline-primary preview-btn";
      preview.textContent = "Preview";
      preview.addEventListener("click", () => togglePreview(item, preview, file));
      actions.appendChild(preview);
    }

    const download = document.createElement("a");
    download.className = "btn btn-sm btn-outline-primary download-link";
    download.href = file.download_url;
    download.textContent = "Download";
    actions.appendChild(download);

    row.append(name, size, actions);
    item.appendChild(row);

    if (file.sha256) {
      const hash = document.createElement("code");
      hash.className = "file-hash d-block mt-1";
      hash.textContent = file.sha256;
      hash.title = file.sha256;
      item.appendChild(hash);
    }

    const media = document.createElement("div");
    media.className = "mt-2 media-container";
    item.appendChild(media);
    list.appendChild(item);
  }

  function togglePreview(item, button, file) {
    const container = item.querySelector(".media-container");
    if (container.firstChild) {
      container.replaceChildren();
      button.textContent = "Preview";
      return;
    }

    let media;
    if (file.preview_kind === "image") {
      media = document.createElement("img");
      media.alt = file.name;
    } else if (file.preview_kind === "video") {
      media = document.createElement("video");
      media.controls = true;
    } else {
      return;
    }
    media.src = file.view_url;
    container.appendChild(media);
    button.textContent = "Hide";
  }

  const copyBtn = document.getElementById("copyBtn");
  const shareInput = document.getElementById("shareableLink");
  copyBtn?.addEventListener("click", async () => {
    if (!shareInput) return;
    try {
      await navigator.clipboard.writeText(shareInput.value);
      copyBtn.textContent = "Copied";
      setTimeout(() => {
        copyBtn.textContent = "Copy";
      }, 1500);
    } catch (_) {
      shareInput.focus();
      shareInput.select();
      document.execCommand("copy");
    }
  });

  const expiry = document.getElementById("expiryInfo");
  const countdown = document.getElementById("timeLeft");
  if (expiry && countdown) {
    let ttl = Number.parseInt(expiry.dataset.ttl || "0", 10);
    let timer = null;
    const tick = () => {
      if (ttl <= 0) {
        countdown.textContent = "(expired)";
        document.querySelectorAll(".preview-btn").forEach((button) => {
          button.disabled = true;
        });
        document.querySelectorAll(".download-link").forEach((link) => {
          link.removeAttribute("href");
          link.setAttribute("aria-disabled", "true");
        });
        if (timer !== null) window.clearInterval(timer);
        return;
      }
      const hours = Math.floor(ttl / 3600);
      const minutes = Math.floor((ttl % 3600) / 60);
      const seconds = ttl % 60;
      const parts = [];
      if (hours) parts.push(`${hours}h`);
      if (minutes) parts.push(`${minutes}m`);
      parts.push(`${seconds}s`);
      countdown.textContent = `(${parts.join(" ")} left)`;
      ttl -= 1;
    };
    tick();
    if (ttl > 0) timer = window.setInterval(tick, 1000);
  }
})();
