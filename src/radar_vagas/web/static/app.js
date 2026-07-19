document.addEventListener("submit", function (event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) {
    return;
  }
  const message = form.dataset.confirm;
  if (message && !window.confirm(message)) {
    event.preventDefault();
    return;
  }
  if (form.classList.contains("resume-upload-panel")) {
    form.classList.add("is-loading");
    form.querySelectorAll("button[type='submit']").forEach(function (button) {
      if (button instanceof HTMLButtonElement) {
        button.disabled = true;
        button.textContent = "Extraindo...";
      }
    });
  }
});

document.querySelectorAll(".resume-dropzone input[type='file']").forEach(function (input) {
  input.addEventListener("change", function () {
    if (!(input instanceof HTMLInputElement) || !input.files || input.files.length === 0) {
      return;
    }
    const dropzone = input.closest(".resume-dropzone");
    const label = dropzone && dropzone.querySelector("span");
    if (label) {
      label.textContent = input.files[0].name;
    }
  });
});

document.addEventListener("click", function (event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const key = target.dataset.repeatAdd;
  if (!key) {
    return;
  }
  const list = document.querySelector(`[data-repeat-list="${key}"]`);
  const row = list && list.querySelector(".repeat-row");
  if (!(list instanceof HTMLElement) || !(row instanceof HTMLElement)) {
    return;
  }
  const clone = row.cloneNode(true);
  if (!(clone instanceof HTMLElement)) {
    return;
  }
  clone.querySelectorAll("input, textarea, select").forEach(function (field) {
    if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
      field.value = "";
    }
    if (field instanceof HTMLSelectElement) {
      field.selectedIndex = 0;
    }
  });
  list.appendChild(clone);
});

const collectionPanel = document.querySelector("[data-collection-status]");
if (collectionPanel) {
  const refreshCollection = function () {
    fetch("/sources/collection-status", { credentials: "same-origin" })
      .then((response) => response.json())
      .then((status) => {
        const state = document.querySelector("[data-collection-state]");
        const message = document.querySelector("[data-collection-message]");
        const found = document.querySelector("[data-collection-found]");
        const created = document.querySelector("[data-collection-created]");
        const errors = document.querySelector("[data-collection-errors]");
        if (state) {
          state.textContent = status.state;
          state.className = `badge ${status.state}`;
        }
        if (message) {
          message.textContent = status.message;
        }
        if (found) {
          found.textContent = status.found;
        }
        if (created) {
          created.textContent = status.created;
        }
        if (errors) {
          errors.textContent = "";
          status.errors.forEach(function (error) {
            const item = document.createElement("p");
            item.className = "notice error";
            item.textContent = error;
            errors.appendChild(item);
          });
        }
        if (status.state === "running") {
          window.setTimeout(refreshCollection, 2000);
        }
      })
      .catch(function () {});
  };
  refreshCollection();
}
