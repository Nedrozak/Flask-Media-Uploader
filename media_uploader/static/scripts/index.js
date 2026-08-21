(() => {
  "use strict";

  const form = document.getElementById("uploadForm");
  const fileInput = document.getElementById("fileInput");
  const chooseFilesBtn = document.getElementById("chooseFilesBtn");
  const fileSelectionSummary = document.getElementById("fileSelectionSummary");
  const fileList = document.getElementById("fileList");
  const uploadBtn = document.getElementById("uploadBtn");
  const cancelBtn = document.getElementById("cancelBtn");
  const statusEl = document.getElementById("uploadStatus");
  if (
    !form ||
    !fileInput ||
    !chooseFilesBtn ||
    !fileSelectionSummary ||
    !fileList ||
    !uploadBtn ||
    !cancelBtn ||
    !statusEl
  ) {
    return;
  }

  const config = {
    sessionId: form.dataset.sessionId,
    maxFileSize: Number(form.dataset.maxFileSize),
    maxSessionBytes: Number(form.dataset.maxSessionBytes),
    maxFiles: Number(form.dataset.maxFiles),
    chunkSize: Number(form.dataset.chunkSize),
    concurrency: Math.max(1, Number(form.dataset.concurrency) || 3),
    timeoutMs: Math.max(1000, Number(form.dataset.requestTimeoutMs) || 180000),
    retries: Math.max(0, Number(form.dataset.chunkRetries) || 0),
    allowedExtensions: new Set((form.dataset.allowedExtensions || "").split(",").filter(Boolean)),
  };

  const selectedFiles = new DataTransfer();
  const uploadRows = new WeakMap();
  const activeRequests = new Set();
  let canceled = false;
  let uploadStarted = false;

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || "";
  }

  function randomUuid() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  function formatBytes(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 2)} ${units[unit]}`;
  }

  function extensionFor(name) {
    const dot = name.lastIndexOf(".");
    return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  }

  function setStatus(message, kind = "") {
    statusEl.textContent = message;
    statusEl.className = `status-message mb-3${kind ? ` status-${kind}` : ""}`;
  }

  function selectionError() {
    const files = Array.from(selectedFiles.files);
    if (files.length > config.maxFiles) return `Select no more than ${config.maxFiles} files.`;
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    if (totalBytes > config.maxSessionBytes) {
      return `The selected files exceed the ${formatBytes(config.maxSessionBytes)} session limit.`;
    }
    return "";
  }

  function refreshUploadButton() {
    const error = selectionError();
    uploadBtn.disabled = selectedFiles.files.length === 0 || Boolean(error);
    if (!uploadStarted) setStatus(error, error ? "error" : "");
  }

  function renderFileList() {
    fileList.replaceChildren();
    const files = Array.from(selectedFiles.files);
    const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
    fileSelectionSummary.textContent =
      files.length === 0
        ? "No files selected"
        : `${files.length} file${files.length === 1 ? "" : "s"} selected • ${formatBytes(totalBytes)}`;

    files.forEach((file, index) => {
      const li = document.createElement("li");
      li.className = "list-group-item d-flex flex-column";
      li.dataset.index = String(index);
      li.dataset.uploadId = randomUuid();
      li.dataset.status = "pending";

      const topRow = document.createElement("div");
      topRow.className = "d-flex align-items-center w-100";

      const filename = document.createElement("span");
      filename.className = "filename-text";
      filename.textContent = file.name;
      filename.title = file.name;

      const size = document.createElement("span");
      size.className = "file-size";
      size.textContent = `(${formatBytes(file.size)})`;

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-sm btn-danger remove-btn";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeSelectedFile(index));

      topRow.append(filename, size, remove);
      li.appendChild(topRow);

      const progress = document.createElement("div");
      progress.className = "progress-shell mt-2";
      progress.hidden = true;
      const bar = document.createElement("progress");
      bar.className = "upload-progress";
      bar.max = 100;
      bar.value = 0;
      bar.setAttribute("aria-label", `Upload progress for ${file.name}`);
      progress.appendChild(bar);
      li.appendChild(progress);

      const info = document.createElement("span");
      info.className = "upload-info";
      info.setAttribute("role", "status");
      info.setAttribute("aria-live", "polite");
      li.appendChild(info);
      uploadRows.set(li, { progress, bar, info });
      fileList.appendChild(li);
    });
    refreshUploadButton();
  }

  function removeSelectedFile(index) {
    const replacement = new DataTransfer();
    Array.from(selectedFiles.files).forEach((file, current) => {
      if (current !== index) replacement.items.add(file);
    });
    while (selectedFiles.items.length) selectedFiles.items.remove(0);
    Array.from(replacement.files).forEach((file) => selectedFiles.items.add(file));
    renderFileList();
  }

  chooseFilesBtn.addEventListener("click", () => {
    if (uploadStarted) return;
    fileInput.click();
  });

  fileInput.addEventListener("change", () => {
    if (uploadStarted) return;
    const rejected = [];
    Array.from(fileInput.files).forEach((file) => {
      const extension = extensionFor(file.name);
      if (!config.allowedExtensions.has(extension)) {
        rejected.push(`${file.name}: unsupported extension`);
        return;
      }
      if (file.size <= 0 || file.size > config.maxFileSize) {
        rejected.push(`${file.name}: exceeds the per-file size limit`);
        return;
      }
      const duplicate = Array.from(selectedFiles.files).some(
        (existing) =>
          existing.name === file.name &&
          existing.size === file.size &&
          existing.lastModified === file.lastModified
      );
      if (!duplicate) selectedFiles.items.add(file);
    });
    renderFileList();
    if (rejected.length) setStatus(rejected.join("; "), "error");
    // The native input is only a picker. Keep the DataTransfer queue authoritative
    // and reset the picker so selecting the same file again still fires "change".
    fileInput.value = "";
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void startUpload();
  });

  cancelBtn.addEventListener("click", () => {
    canceled = true;
    activeRequests.forEach((xhr) => xhr.abort());
    activeRequests.clear();
    setStatus("Upload canceled. You can retry the unfinished files.", "error");
  });

  async function startUpload() {
    const files = Array.from(selectedFiles.files);
    if (!files.length || selectionError()) return;

    canceled = false;
    uploadStarted = true;
    uploadBtn.disabled = true;
    uploadBtn.textContent = "Uploading…";
    cancelBtn.hidden = false;
    chooseFilesBtn.disabled = true;
    fileInput.disabled = true;
    document.querySelectorAll(".remove-btn").forEach((button) => {
      button.disabled = true;
    });
    setStatus("Uploading files…");

    const jobs = files
      .map((file, index) => ({ file, index, li: fileList.querySelector(`li[data-index="${index}"]`) }))
      .filter((job) => job.li && job.li.dataset.status !== "success");

    let cursor = 0;
    const failures = [];
    async function worker() {
      while (!canceled) {
        const jobIndex = cursor;
        cursor += 1;
        if (jobIndex >= jobs.length) return;
        const job = jobs[jobIndex];
        try {
          await uploadFileInChunks(job.file, job.li);
          job.li.dataset.status = "success";
          const row = uploadRows.get(job.li);
          if (row) {
            row.info.textContent = "Uploaded";
            row.info.classList.remove("upload-info-error");
            row.info.classList.add("upload-info-success");
          }
        } catch (error) {
          if (canceled) return;
          const message = error instanceof Error ? error.message : String(error);
          job.li.dataset.status = "failed";
          const row = uploadRows.get(job.li);
          if (row) {
            row.info.textContent = `Error: ${message}`;
            row.info.classList.remove("upload-info-success");
            row.info.classList.add("upload-info-error");
            row.bar.classList.add("upload-progress-error");
          }
          failures.push(job.file.name);
        }
      }
    }

    const workerCount = Math.min(config.concurrency, Math.max(1, jobs.length));
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
    cancelBtn.hidden = true;

    if (canceled) {
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Retry unfinished uploads";
      return;
    }
    if (failures.length) {
      setStatus(
        `${failures.length} file${failures.length === 1 ? "" : "s"} failed. Review the file errors below and retry.`,
        "error"
      );
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Retry unfinished uploads";
      return;
    }

    await finalizeSession();
  }

  async function uploadFileInChunks(file, li) {
    const totalChunks = Math.ceil(file.size / config.chunkSize);
    const transferId = li.dataset.uploadId;
    const startedAt = performance.now();
    const row = uploadRows.get(li);
    if (!row) throw new Error("Upload row is unavailable.");
    const { progress, bar, info } = row;
    progress.hidden = false;
    bar.classList.remove("upload-progress-error");
    info.classList.remove("upload-info-error", "upload-info-success");

    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
      if (canceled) throw new Error("Canceled");
      const start = chunkIndex * config.chunkSize;
      const end = Math.min(start + config.chunkSize, file.size);
      const blob = file.slice(start, end);
      const formData = new FormData();
      formData.append("session_id", config.sessionId);
      formData.append("original_filename", file.name);
      formData.append("fileUuid", transferId);
      formData.append("file_size", String(file.size));
      formData.append("chunk_index", String(chunkIndex));
      formData.append("total_chunks", String(totalChunks));
      formData.append("chunk", blob, "chunk.bin");

      const result = await sendChunkWithRetry(
        formData,
        file,
        chunkIndex,
        totalChunks,
        bar,
        info,
        startedAt
      );
      if (result.status === "success") {
        bar.value = 100;
        bar.textContent = "100%";
        return;
      }
      if (result.status !== "chunk_received") {
        throw new Error("Upload server returned an unexpected chunk status.");
      }
      if (chunkIndex === totalChunks - 1) {
        throw new Error("The server did not confirm that all file chunks were received. Retry the upload.");
      }
    }
  }

  async function sendChunkWithRetry(formData, file, chunkIndex, totalChunks, bar, info, startedAt) {
    let lastError;
    for (let attempt = 0; attempt <= config.retries; attempt += 1) {
      try {
        return await sendChunkXHR(formData, file, chunkIndex, totalChunks, bar, info, startedAt);
      } catch (error) {
        lastError = error;
        if (canceled || !error.retryable || attempt === config.retries) throw error;
        await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
      }
    }
    throw lastError;
  }

  function sendChunkXHR(formData, file, chunkIndex, totalChunks, bar, info, startedAt) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      activeRequests.add(xhr);
      xhr.open("POST", "/api/uploads/chunks", true);
      xhr.timeout = config.timeoutMs;
      xhr.setRequestHeader("X-CSRFToken", csrfToken());

      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        const currentBytes = Math.min(file.size, chunkIndex * config.chunkSize + event.loaded);
        const percent = Math.min(100, (currentBytes / file.size) * 100);
        bar.value = percent;
        bar.textContent = `${Math.floor(percent)}%`;

        const elapsed = Math.max(0.001, (performance.now() - startedAt) / 1000);
        const speed = currentBytes / elapsed;
        const eta = speed > 0 ? (file.size - currentBytes) / speed : 0;
        info.textContent = `${formatBytes(speed)}/s • ETA ${formatDuration(eta)} • chunk ${chunkIndex + 1}/${totalChunks}`;
      });

      xhr.addEventListener("load", () => {
        activeRequests.delete(xhr);
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const body = JSON.parse(xhr.responseText);
            if (!body || typeof body.status !== "string") {
              throw new Error("missing upload status");
            }
            resolve(body);
          } catch (_) {
            const error = new Error("Upload server returned an invalid response.");
            error.retryable = true;
            reject(error);
          }
          return;
        }
        reject(httpError(xhr));
      });
      xhr.addEventListener("error", () => {
        activeRequests.delete(xhr);
        const error = new Error("Network error");
        error.retryable = true;
        reject(error);
      });
      xhr.addEventListener("timeout", () => {
        activeRequests.delete(xhr);
        const error = new Error("Upload request timed out");
        error.retryable = true;
        reject(error);
      });
      xhr.addEventListener("abort", () => {
        activeRequests.delete(xhr);
        const error = new Error("Canceled");
        error.retryable = false;
        reject(error);
      });
      xhr.send(formData);
    });
  }

  function httpError(xhr) {
    let message = `Upload failed (${xhr.status})`;
    try {
      const body = JSON.parse(xhr.responseText);
      message = body?.error?.message || body?.error || message;
    } catch (_) {
      // Keep the generic message for non-JSON responses.
    }
    const error = new Error(message);
    error.retryable = xhr.status === 409 || xhr.status === 429 || xhr.status >= 500;
    return error;
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
    if (seconds < 60) return `${Math.ceil(seconds)}s`;
    return `${Math.floor(seconds / 60)}m ${Math.ceil(seconds % 60)}s`;
  }

  async function finalizeSession() {
    uploadBtn.textContent = "Finalizing…";
    setStatus("Finalizing share…");
    let lastError = new Error("Could not finalize the upload.");

    for (let attempt = 0; attempt <= config.retries; attempt += 1) {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), config.timeoutMs);
      try {
        const response = await fetch("/api/uploads/finalize", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken(),
          },
          body: JSON.stringify({ session_id: config.sessionId }),
          signal: controller.signal,
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !body.shareable_url) {
          const error = new Error(body?.error?.message || `Finalization failed (${response.status})`);
          error.retryable = response.status === 409 || response.status === 429 || response.status >= 500;
          throw error;
        }
        window.location.assign(body.shareable_url);
        return;
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error));
        const retryable =
          lastError.name === "AbortError" || lastError instanceof TypeError || lastError.retryable === true;
        if (!retryable || attempt === config.retries) break;
        setStatus("Finalization was interrupted; retrying…");
        await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
      } finally {
        window.clearTimeout(timeout);
      }
    }

    setStatus(lastError.message, "error");
    uploadBtn.disabled = false;
    uploadBtn.textContent = "Retry finalization";
  }
})();
