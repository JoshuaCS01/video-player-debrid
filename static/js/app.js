document.addEventListener("DOMContentLoaded", () => {
  const mediaGrid = document.getElementById("videoList");
  const statusBatchUrl = mediaGrid?.dataset.thumbnailStatusBatch;
  const isHereSphere = /HereSphere/i.test(navigator.userAgent);
  if (isHereSphere) {
    document.querySelectorAll("[data-play-link]").forEach((link) => link.setAttribute("download", ""));
    const help = document.querySelector("[data-heresphere-help]");
    if (help) help.hidden = false;
  }
  const pending = new Map();
  let pollTimer = null;
  let pollRound = 0;
  let polling = false;

  const schedulePoll = () => {
    if (!statusBatchUrl || pending.size === 0 || pollTimer || polling) return;
    const delays = [3000, 4000, 6000, 8000, 10000];
    const delay = document.hidden ? 12000 : delays[Math.min(pollRound, delays.length - 1)];
    pollTimer = window.setTimeout(pollStatuses, delay);
  };

  const pollStatuses = async () => {
    pollTimer = null;
    if (pending.size === 0 || polling) return;
    polling = true;
    const ids = [...pending.keys()];
    try {
      const response = await fetch(`${statusBatchUrl}?ids=${ids.join(",")}`, { cache: "no-store" });
      const payload = await response.json();
      ids.forEach((id) => {
        const image = pending.get(id);
        const status = payload.items?.[id];
        if (!image || !status) return;
        if (status.state === "ready") {
          pending.delete(id);
          image.src = `${image.dataset.thumbnailSrc}?ready=${Date.now()}`;
          image.dataset.thumbnailCached = "true";
        } else if (status.state === "failed") {
          pending.delete(id);
        }
      });
      pollRound = pending.size ? pollRound + 1 : 0;
    } catch (_error) {
      pollRound += 1;
    } finally {
      polling = false;
      schedulePoll();
    }
  };

  const requestThumbnail = (image) => {
    if (image.dataset.thumbnailCached === "true" || image.dataset.thumbnailRequested === "true") return;
    image.dataset.thumbnailRequested = "true";
    image.addEventListener("load", () => {
      pending.set(image.dataset.thumbnailId, image);
      schedulePoll();
    }, { once: true });
    image.src = image.dataset.thumbnailSrc;
  };

  const uncachedImages = [...document.querySelectorAll("img[data-thumbnail-cached='false']")];
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        requestThumbnail(entry.target);
        observer.unobserve(entry.target);
      });
    }, { rootMargin: "600px 0px", threshold: 0.01 });
    uncachedImages.forEach((image) => observer.observe(image));
  } else {
    uncachedImages.slice(0, 12).forEach(requestThumbnail);
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && pending.size) schedulePoll();
  });

  document.addEventListener("click", async (event) => {
    const toggle = event.target.closest("[data-group-url]");
    if (!toggle) return;
    const container = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!container) return;

    const expanded = toggle.getAttribute("aria-expanded") === "true";
    if (container.dataset.loaded === "true") {
      toggle.setAttribute("aria-expanded", String(!expanded));
      container.hidden = expanded;
      return;
    }

    toggle.disabled = true;
    toggle.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(toggle.dataset.groupUrl, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      container.innerHTML = await response.text();
      container.dataset.loaded = "true";
      container.hidden = false;
      toggle.setAttribute("aria-expanded", "true");
    } catch (_error) {
      container.innerHTML = '<p class="group-load-error" role="alert">Unable to load these videos. Please try again.</p>';
      container.hidden = false;
    } finally {
      toggle.disabled = false;
      toggle.removeAttribute("aria-busy");
    }
  });

  const search = document.querySelector("[data-media-search]");
  if (!search) return;
  const items = [...document.querySelectorAll("[data-media-item]")];
  const empty = document.getElementById("emptyState");
  const count = document.getElementById("visibleCount");

  const filter = () => {
    const terms = search.value.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
    let visible = 0;
    items.forEach((item) => {
      const haystack = item.dataset.search.toLocaleLowerCase();
      const matches = terms.every((term) => haystack.includes(term));
      item.hidden = !matches;
      if (matches) visible += 1;
    });
    if (empty) empty.hidden = visible !== 0;
    if (count) count.textContent = visible;
  };
  search.addEventListener("input", filter);
});
