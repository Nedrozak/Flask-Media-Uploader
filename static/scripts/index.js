const { MAX_FILE_SIZE, CHUNK_SIZE, SESSION_ID } = window.UPLOAD_CONFIG;

const dt = new DataTransfer();
const fileInput = document.getElementById("fileInput");
const fileList = document.getElementById("fileList");
const uploadForm = document.getElementById("uploadForm");
const uploadBtn = document.getElementById("uploadBtn");

// On Windows, pre-filter Explorer to only images/videos
const isWindows =
  (navigator.userAgentData?.platform?.startsWith("Windows") ?? false) ||
  navigator.userAgent.includes("Windows");
if (isWindows) {
  fileInput.accept = "image/*,video/*";
}

function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute("content") : "";
}

// Polyfill crypto.randomUUID() if missing
if (typeof crypto.randomUUID !== "function") {
  crypto.randomUUID = function () {
    // RFC4122 v4 compliant UUID generator
    return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, (c) =>
      (
        c ^
        (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))
      ).toString(16)
    );
  };
}

// Convert bytes → "XX.XX MB"
function formatFileSize(bytes) {
  const mb = bytes / (1024 * 1024);
  return mb.toFixed(2) + " MB";
}

// Build the <li> elements showing filename, size, badge, remove button, plus placeholders for progress & info
function renderFileList() {
  fileList.innerHTML = "";

  Array.from(dt.files).forEach((file, index) => {
    const li = document.createElement("li");
    li.className = "list-group-item d-flex flex-column";
    li.dataset.index = index;

    // Top row: filename, size, (too-large), Remove button
    const topRow = document.createElement("div");
    topRow.className = "d-flex align-items-center";

    // Filename (truncated)
    const filenameSpan = document.createElement("span");
    filenameSpan.className = "filename-text";
    filenameSpan.textContent = file.name;
    filenameSpan.title = file.name;

    // File size
    const sizeSpan = document.createElement("span");
    sizeSpan.className = "input-group-text file-size text-muted";
    sizeSpan.textContent = "(" + formatFileSize(file.size) + ")";

    topRow.appendChild(filenameSpan);
    topRow.appendChild(sizeSpan);

    // “Too large” badge, if needed
    if (file.size > MAX_FILE_SIZE) {
      filenameSpan.classList.add("text-danger");
      const warnBadge = document.createElement("span");
      warnBadge.className = "badge bg-danger too-large-badge";
      warnBadge.textContent = "Too large";
      topRow.appendChild(warnBadge);
    }

    // Remove button (only before upload starts)
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn btn-sm btn-danger remove-btn";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", () => {
      // Rebuild DataTransfer without this file
      const dtNew = new DataTransfer();
      Array.from(dt.files).forEach((f, i) => {
        if (i !== index) {
          dtNew.items.add(f);
        }
      });
      while (dt.items.length > 0) {
        dt.items.remove(0);
      }
      Array.from(dtNew.files).forEach((f) => dt.items.add(f));

      fileInput.files = dt.files;
      renderFileList();

      // never allow upload if there’s an oversized file
      const hasTooLarge = Array.from(dt.files).some(
        (f) => f.size > MAX_FILE_SIZE
      );
      uploadBtn.disabled = dt.files.length === 0 || hasTooLarge;
    });
    topRow.appendChild(removeBtn);

    li.appendChild(topRow);

    // Progress bar
    const progressContainer = document.createElement("div");
    progressContainer.className = "progress mt-2";
    progressContainer.style.display = "none";

    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";
    progressBar.setAttribute("role", "progressbar");
    progressBar.setAttribute("aria-valuemin", "0");
    progressBar.setAttribute("aria-valuemax", "100");
    progressBar.setAttribute("aria-valuenow", "0");
    progressBar.style.width = "0%";
    progressBar.textContent = ""; // optional
    progressContainer.appendChild(progressBar);

    li.appendChild(progressContainer);

    // Upload info (speed + ETA)
    const infoSpan = document.createElement("span");
    infoSpan.className = "upload-info";
    infoSpan.style.display = "none";
    li.appendChild(infoSpan);

    // Store references
    li.progressContainer = progressContainer;
    li.progressBar = progressBar;
    li.infoSpan = infoSpan;
    li.removeBtn = removeBtn;

    fileList.appendChild(li);

    uploadBtn.disabled = dt.files.length === 0;
  });

  // Update the actual <input> so the form, if submitted normally, matches dt.files
  fileInput.files = dt.files;
}

// Whenever the user picks files, add them to dt without duplicates, then re-render
fileInput.addEventListener("change", () => {
  Array.from(fileInput.files).forEach((file) => {
    if (!file.type.startsWith("image/") && !file.type.startsWith("video/")) {
      alert(`“${file.name}” is not an image or video and was skipped.`);
      return;
    }

    // Prevent selecting too-large files
    if (file.size > MAX_FILE_SIZE) {
      alert(
        `“${file.name}” is ${formatFileSize(file.size)}, ` +
          `but the max is ${formatFileSize(MAX_FILE_SIZE)}.`
      );
      return;
    }

    // Prevent duplicates
    const exists = Array.from(dt.files).some(
      (f) =>
        f.name === file.name &&
        f.size === file.size &&
        f.lastModified === file.lastModified
    );
    if (!exists) {
      dt.items.add(file);
    }
  });

  renderFileList();

  // Change “Browse” → “Add more files” on supported browsers
  if (fileInput.setAttribute) {
    fileInput.title = "Add more files";
    fileInput.classList.add("add-more");
  }
});

// Intercept the form submission to do per-file chunked XHR uploads
uploadForm.addEventListener("submit", function (e) {
  e.preventDefault();
  if (!dt.files.length) {
    alert(
      "You haven't selected any files. Please add at least one file before uploading."
    );
    return;
  }
  startUpload();
});

function startUpload() {
  const files = Array.from(dt.files);
  let completedCount = 0;

  // Disable Remove buttons and Upload button so user can’t change mid-upload
  uploadBtn.disabled = true;
  document
    .querySelectorAll("#fileList .remove-btn")
    .forEach((btn) => (btn.disabled = true));
  fileInput.disabled = true;

  files.forEach((file, idx) => {
    const li = fileList.querySelector(`li[data-index='${idx}']`);
    if (!li) return;

    // Show progress bar & info
    li.progressContainer.style.display = "block";
    li.infoSpan.style.display = "inline";

    // Kick off chunked upload for this file
    uploadFileInChunks(file, idx, li)
      .then(() => {
        completedCount++;
        if (completedCount === files.length) {
          finalizeSession();
        }
      })
      .catch((errMsg) => {
        li.infoSpan.textContent = "Error: " + errMsg;
        li.progressBar.classList.add("bg-danger");
        // Still count as “completed” so we don’t hang if one fails
        completedCount++;
        if (completedCount === files.length) {
          finalizeSession();
        }
      });
  });
}

async function uploadFileInChunks(file, index, li) {
  return new Promise(async (resolve, reject) => {
    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    const fileUuid = crypto.randomUUID();
    const startTime = performance.now();

    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
      const start = chunkIndex * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const blob = file.slice(start, end);

      const formData = new FormData();
      formData.append("session_id", SESSION_ID);
      formData.append("original_filename", file.name);
      formData.append("fileUuid", fileUuid);
      formData.append("chunk_index", chunkIndex);
      formData.append("total_chunks", totalChunks);
      formData.append("chunk", blob);

      try {
        await sendChunkXHR(
          formData,
          file,
          chunkIndex,
          totalChunks,
          li,
          startTime
        );
      } catch (err) {
        return reject(err);
      }
    }

    resolve();
  });
}

function sendChunkXHR(formData, file, chunkIndex, totalChunks, li, startTime) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/upload-ajax", true);
    xhr.setRequestHeader("X-CSRFToken", getCSRFToken());

    xhr.upload.onprogress = function (e) {
      if (!e.lengthComputable) return;

      // “prevBytes” = how many full chunks were already sent
      const prevBytes = chunkIndex * CHUNK_SIZE;

      // “e.loaded” = how many bytes of THIS chunk have uploaded so far
      const currentLoaded = prevBytes + e.loaded;

      const percent = ((currentLoaded / file.size) * 100).toFixed(2);
      console.log(
        `Chunk ${chunkIndex + 1}/${totalChunks} (${currentLoaded}/${
          file.size
        } bytes) - ${percent}%`
      );

      li.progressBar.style.width = `${percent}%`;
      li.progressBar.setAttribute("aria-valuenow", `${Math.floor(percent)}`);
      li.progressBar.textContent = `${Math.floor(percent)}%`;

      const elapsedSec = (performance.now() - startTime) / 1000;
      const speed = currentLoaded / elapsedSec; // bytes/sec
      const remainingBytes = file.size - currentLoaded;
      const etaSec = speed > 0 ? remainingBytes / speed : 0;

      let speedText;
      if (speed < 1024 * 1024) {
        speedText = (speed / 1024).toFixed(2) + " KB/s";
      } else {
        speedText = (speed / (1024 * 1024)).toFixed(2) + " MB/s";
      }
      const etaText =
        etaSec < 60
          ? etaSec.toFixed(1) + "s"
          : Math.floor(etaSec / 60) + "m " + Math.round(etaSec % 60) + "s";

      li.infoSpan.textContent = speedText + " • ETA " + etaText;
    };

    xhr.onload = function () {
      if (xhr.status === 200) {
        resolve();
      } else {
        let errMsg = "Upload failed";
        try {
          const resp = JSON.parse(xhr.responseText);
          if (resp.error) errMsg = resp.error;
        } catch (_) {}
        reject(errMsg);
      }
    };

    xhr.onerror = function () {
      reject("Network error");
    };

    xhr.send(formData);
  });
}

function finalizeSession() {
  fetch("/finalize-upload-session", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCSRFToken(),
    },
    body: JSON.stringify({ session_id: SESSION_ID }),
  })
    .then((res) => res.json())
    .then((data) => {
      if (data.token) {
        window.location.href = `/${data.token}`;
      } else {
        alert("Failed to finalize upload session");
        console.error(data);
      }
    })
    .catch((err) => {
      alert("Error finalizing upload session");
      console.error(err);
    });
}
