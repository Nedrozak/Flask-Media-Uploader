document.addEventListener("DOMContentLoaded", function () {
  const listContainer = document.getElementById("fileList");

  // Guard against malformed `uploadedFilesInfo`
  if (!uploadedFilesInfo || !Array.isArray(uploadedFilesInfo)) {
    const noFilesItem = document.createElement("li");
    noFilesItem.className = "list-group-item text-danger";
    noFilesItem.textContent = "No files to display.";
    document.getElementById("fileList").appendChild(noFilesItem);
    return;
  }

  // Helper: format bytes → "XX.XX MB"
  function formatFileSize(bytes) {
    const mb = bytes / (1024 * 1024);
    return mb.toFixed(2) + " MB";
  }

  uploadedFilesInfo.forEach((fileObj) => {
    if (!fileObj || typeof fileObj.name !== "string") return; // skip anything not a string

    const relPath = fileObj.path;
    const baseName = fileObj.name;
    const sizeText = formatFileSize(fileObj.size);

    const li = document.createElement("li");
    li.className = "list-group-item";
    li.setAttribute("data-path", relPath);

    const inputGroup = document.createElement("div");
    inputGroup.className = "input-group";

    // filename
    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.className = "form-control filename-text";
    nameInput.value = baseName;
    nameInput.readOnly = true;
    nameInput.title = baseName;

    // span for file size
    const sizeSpan = document.createElement("span");
    sizeSpan.className = "input-group-text file-size text-muted";
    sizeSpan.textContent = `(${sizeText})`;

    const btnContainer = document.createElement("div");
    btnContainer.className = "input-group-append";

    const playBtn = document.createElement("button");
    playBtn.className = "btn btn-outline-primary play-btn";
    playBtn.type = "button";
    playBtn.innerHTML = `
      <i class="bi bi-eye fs-5" aria-hidden="true"></i>
      <span class="visually-hidden">Show</span>
    `;

    const downloadBtn = document.createElement("a");
    downloadBtn.className = "btn btn-outline-primary";
    downloadBtn.href = `/uploads/${encodeURIComponent(fileObj.path)}`;
    downloadBtn.download = baseName;
    downloadBtn.innerHTML = `
      <i class="bi bi-download fs-5" aria-hidden="true"></i>
      <span class="visually-hidden">Download</span>
    `;

    btnContainer.appendChild(playBtn);
    btnContainer.appendChild(downloadBtn);

    inputGroup.appendChild(nameInput);
    inputGroup.appendChild(sizeSpan);
    inputGroup.appendChild(btnContainer);
    li.appendChild(inputGroup);

    // placeholder for the media
    const mediaContainer = document.createElement("div");
    mediaContainer.className = "mt-2 media-container";
    li.appendChild(mediaContainer);

    document.getElementById("fileList").appendChild(li);
  });

  listContainer.addEventListener("click", function (event) {
    const btn = event.target.closest(".play-btn");
    if (!btn) return;
    const li = btn.closest("li");
    const filename = li.getAttribute("data-path");
    const mediaContainer = li.querySelector(".media-container");
    if (!mediaContainer) return;

    // Prevent re-inserting the same media twice
    if (mediaContainer.firstChild) {
      mediaContainer.innerHTML = "";
      btn.innerHTML = `
      <i class="bi bi-eye fs-5" aria-hidden="true"></i>
      <span class="visually-hidden">Show</span>
    `;
      return;
    }

    const ext = filename.split(".").pop().toLowerCase();
    let mediaEl;
    if (["mp4", "webm", "ogg"].includes(ext)) {
      mediaEl = document.createElement("video");
      mediaEl.setAttribute("controls", "");
      mediaEl.style.maxWidth = "100%";
      mediaEl.src = `/uploads/${encodeURIComponent(filename)}`;
    } else if (["png", "jpg", "jpeg", "gif"].includes(ext)) {
      mediaEl = document.createElement("img");
      mediaEl.style.maxWidth = "100%";
      mediaEl.src = `/uploads/${encodeURIComponent(filename)}`;
    } else {
      // unsupported file type—just do nothing
      return;
    }

    mediaContainer.appendChild(mediaEl);
    btn.innerHTML = `
      <i class="bi bi-eye-slash fs-5" aria-hidden="true"></i>
      <span class="visually-hidden">Hide</span>
    `;
  });

  // Copy‐to‐clipboard
  const copyBtn = document.getElementById("copyBtn");
  const shareInput = document.getElementById("shareableLink");

  if (copyBtn && shareInput) {
    copyBtn.addEventListener("click", function () {
      const textToCopy = shareInput.value;

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard
          .writeText(textToCopy)
          .then(() => {
            copyBtn.textContent = "Copied!";
            copyBtn.disabled = true;
            setTimeout(() => {
              copyBtn.textContent = "Copy";
              copyBtn.disabled = false;
            }, 1500);
          })
          .catch(() => {
            console.error("Clipboard write failed:", err);
            alert("Copy failed—permission denied or insecure context.");
          });
      } else {
        console.error("Clipboard API not supported in this browser.");
        alert("Your browser doesn't support copying to clipboard.");
      }
    });
  }

  (function initExpiryCountdown() {
    const expiryEl = document.getElementById("expiryInfo");
    if (!expiryEl) return;
    let ttl = parseInt(expiryEl.dataset.ttl, 10);
    const display = document.getElementById("timeLeft");

    function fmt(h, m, s) {
      const parts = [];
      if (h) parts.push(h + "h");
      if (m) parts.push(m + "m");
      parts.push(s + "s");
      return parts.join(" ");
    }

    function tick() {
      if (ttl <= 0) {
        display.textContent = "expired";
        return clearInterval(timer);
      }
      const h = Math.floor(ttl / 3600);
      const m = Math.floor((ttl % 3600) / 60);
      const s = ttl % 60;
      display.textContent = "(" + fmt(h, m, s) + " left)";
      ttl--;
    }

    tick();
    const timer = setInterval(tick, 1000);
  })();
});
