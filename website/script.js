/**
 * LofiMellowHQ — Official Artist Website
 * Vanilla ES6 · Cloudflare Pages static
 * https://LofiMellowHQ.studio
 */
(function () {
  "use strict";

  /* ========================================================================
     Utilities
     ======================================================================== */
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  const prefersReducedMotion = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const formatTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  };

  /* ========================================================================
     Sticky header
     ======================================================================== */
  function initHeader() {
    const header = $(".site-header");
    if (!header) return;

    const onScroll = () => {
      header.classList.toggle("is-scrolled", window.scrollY > 24);
    };

    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ========================================================================
     Mobile navigation — animated hamburger + expand
     ======================================================================== */
  function initNav() {
    const toggle = $(".nav-toggle");
    const menu = $(".nav-menu");
    if (!toggle || !menu) return;

    const setOpen = (open) => {
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      menu.classList.toggle("is-open", open);
      document.body.style.overflow = open ? "hidden" : "";
    };

    toggle.addEventListener("click", () => {
      const open = toggle.getAttribute("aria-expanded") !== "true";
      setOpen(open);
    });

    // Close on link click (mobile)
    $$(".nav-link", menu).forEach((link) => {
      link.addEventListener("click", () => setOpen(false));
    });

    // Close on Escape
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
        setOpen(false);
        toggle.focus();
      }
    });

    // Close when resizing to desktop
    window.addEventListener(
      "resize",
      () => {
        if (window.innerWidth > 900) setOpen(false);
      },
      { passive: true }
    );
  }

  /* ========================================================================
     Hero particles + rain ambience
     ======================================================================== */
  function initHeroAmbience() {
    if (prefersReducedMotion()) return;

    const particleHost = $(".hero-particles");
    const rainHost = $(".hero-rain");

    if (particleHost) {
      const count = window.innerWidth < 640 ? 28 : 48;
      const frag = document.createDocumentFragment();
      for (let i = 0; i < count; i++) {
        const s = document.createElement("span");
        const size = 1.5 + Math.random() * 2.5;
        s.style.width = `${size}px`;
        s.style.height = `${size}px`;
        s.style.left = `${Math.random() * 100}%`;
        s.style.animationDuration = `${10 + Math.random() * 18}s`;
        s.style.animationDelay = `${Math.random() * 12}s`;
        s.style.opacity = String(0.15 + Math.random() * 0.4);
        if (Math.random() > 0.6) {
          s.style.background = "var(--primary)";
          s.style.boxShadow = "0 0 6px var(--primary-glow)";
        } else if (Math.random() > 0.8) {
          s.style.background = "var(--secondary)";
        }
        frag.appendChild(s);
      }
      particleHost.appendChild(frag);
    }

    if (rainHost) {
      const drops = window.innerWidth < 640 ? 24 : 42;
      const frag = document.createDocumentFragment();
      for (let i = 0; i < drops; i++) {
        const s = document.createElement("span");
        s.style.left = `${Math.random() * 100}%`;
        s.style.animationDuration = `${0.7 + Math.random() * 0.9}s`;
        s.style.animationDelay = `${Math.random() * 2}s`;
        s.style.height = `${24 + Math.random() * 40}px`;
        s.style.opacity = String(0.25 + Math.random() * 0.45);
        frag.appendChild(s);
      }
      rainHost.appendChild(frag);
    }
  }

  /* ========================================================================
     Scroll reveal
     ======================================================================== */
  function initReveal() {
    const els = $$(".reveal");
    if (!els.length) return;

    if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
      els.forEach((el) => el.classList.add("is-visible"));
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            io.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );

    els.forEach((el) => io.observe(el));
  }

  /* ========================================================================
     Custom HTML5 Audio Player
     ======================================================================== */
  function initAudioPlayer() {
    const root = $("[data-audio-player]");
    if (!root) return;

    const audio = $("audio", root);
    const playBtn = $("[data-play]", root);
    const progressBar = $("[data-progress]", root);
    const progressFill = $("[data-progress-fill]", root);
    const progressThumb = $("[data-progress-thumb]", root);
    const currentTimeEl = $("[data-current]", root);
    const durationEl = $("[data-duration]", root);
    const volumeSlider = $("[data-volume]", root);
    const volumeBtn = $("[data-mute]", root);
    const titleEl = $("[data-track-title]", root);
    const artistEl = $("[data-track-artist]", root);
    const artEl = $("[data-track-art]", root);
    const trackButtons = $$("[data-track]", root);

    if (!audio || !playBtn) return;

    let isSeeking = false;
    let lastVolume = 0.85;

    audio.volume = volumeSlider ? parseFloat(volumeSlider.value) || 0.85 : 0.85;

    const updateProgress = () => {
      if (isSeeking) return;
      const pct =
        audio.duration && Number.isFinite(audio.duration)
          ? (audio.currentTime / audio.duration) * 100
          : 0;
      if (progressFill) progressFill.style.width = `${pct}%`;
      if (progressThumb) progressThumb.style.left = `${pct}%`;
      if (currentTimeEl) currentTimeEl.textContent = formatTime(audio.currentTime);
    };

    const setPlayingUI = (playing) => {
      playBtn.classList.toggle("is-playing", playing);
      playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
    };

    const loadTrack = (btn) => {
      const src = btn.getAttribute("data-src");
      const title = btn.getAttribute("data-title") || "Untitled";
      const artist = btn.getAttribute("data-artist") || "LofiMellowHQ";
      const art = btn.getAttribute("data-art");
      const wasPlaying = !audio.paused;

      trackButtons.forEach((b) => {
        b.classList.toggle("is-active", b === btn);
        b.setAttribute("aria-current", b === btn ? "true" : "false");
      });

      if (titleEl) titleEl.textContent = title;
      if (artistEl) artistEl.textContent = artist;
      if (artEl && art) {
        artEl.src = art;
        artEl.alt = `${title} cover art`;
      }

      audio.src = src;
      audio.load();

      if (wasPlaying) {
        audio.play().catch(() => setPlayingUI(false));
      }
    };

    playBtn.addEventListener("click", () => {
      if (audio.paused) {
        audio.play().catch(() => {
          /* Autoplay may be blocked until user gesture — already a gesture */
        });
      } else {
        audio.pause();
      }
    });

    audio.addEventListener("play", () => setPlayingUI(true));
    audio.addEventListener("pause", () => setPlayingUI(false));
    audio.addEventListener("ended", () => {
      setPlayingUI(false);
      // Auto-advance to next track if available
      const active = trackButtons.find((b) => b.classList.contains("is-active"));
      if (active) {
        const idx = trackButtons.indexOf(active);
        const next = trackButtons[idx + 1];
        if (next) {
          loadTrack(next);
          audio.play().catch(() => {});
        } else {
          audio.currentTime = 0;
          updateProgress();
        }
      }
    });

    audio.addEventListener("timeupdate", updateProgress);
    audio.addEventListener("loadedmetadata", () => {
      if (durationEl) durationEl.textContent = formatTime(audio.duration);
      updateProgress();
    });

    // Seek on progress bar
    const seekFromEvent = (clientX) => {
      if (!progressBar || !audio.duration) return;
      const rect = progressBar.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      audio.currentTime = ratio * audio.duration;
      const pct = ratio * 100;
      if (progressFill) progressFill.style.width = `${pct}%`;
      if (progressThumb) progressThumb.style.left = `${pct}%`;
      if (currentTimeEl) currentTimeEl.textContent = formatTime(audio.currentTime);
    };

    if (progressBar) {
      progressBar.setAttribute("role", "slider");
      progressBar.setAttribute("aria-label", "Seek");
      progressBar.setAttribute("aria-valuemin", "0");
      progressBar.setAttribute("aria-valuemax", "100");
      progressBar.setAttribute("tabindex", "0");

      progressBar.addEventListener("click", (e) => seekFromEvent(e.clientX));

      progressBar.addEventListener("keydown", (e) => {
        if (!audio.duration) return;
        const step = e.shiftKey ? 10 : 5;
        if (e.key === "ArrowRight" || e.key === "ArrowUp") {
          e.preventDefault();
          audio.currentTime = Math.min(audio.duration, audio.currentTime + step);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
          e.preventDefault();
          audio.currentTime = Math.max(0, audio.currentTime - step);
        } else if (e.key === "Home") {
          e.preventDefault();
          audio.currentTime = 0;
        } else if (e.key === "End") {
          e.preventDefault();
          audio.currentTime = audio.duration;
        }
        updateProgress();
      });

      // Pointer drag seek
      progressBar.addEventListener("pointerdown", (e) => {
        isSeeking = true;
        progressBar.setPointerCapture(e.pointerId);
        seekFromEvent(e.clientX);
      });

      progressBar.addEventListener("pointermove", (e) => {
        if (!isSeeking) return;
        seekFromEvent(e.clientX);
      });

      const endSeek = (e) => {
        if (!isSeeking) return;
        isSeeking = false;
        try {
          progressBar.releasePointerCapture(e.pointerId);
        } catch (_) {
          /* ignore */
        }
      };

      progressBar.addEventListener("pointerup", endSeek);
      progressBar.addEventListener("pointercancel", endSeek);
    }

    // Volume
    if (volumeSlider) {
      volumeSlider.addEventListener("input", () => {
        const v = parseFloat(volumeSlider.value);
        audio.volume = v;
        audio.muted = v === 0;
        lastVolume = v > 0 ? v : lastVolume;
        updateMuteIcon();
      });
    }

    const updateMuteIcon = () => {
      if (!volumeBtn) return;
      const muted = audio.muted || audio.volume === 0;
      volumeBtn.setAttribute("aria-label", muted ? "Unmute" : "Mute");
      volumeBtn.classList.toggle("is-muted", muted);
      const unmuted = volumeBtn.querySelector(".icon-volume");
      const mutedIcon = volumeBtn.querySelector(".icon-muted");
      if (unmuted) unmuted.hidden = muted;
      if (mutedIcon) mutedIcon.hidden = !muted;
    };

    if (volumeBtn) {
      volumeBtn.addEventListener("click", () => {
        if (audio.muted || audio.volume === 0) {
          audio.muted = false;
          audio.volume = lastVolume || 0.85;
          if (volumeSlider) volumeSlider.value = String(audio.volume);
        } else {
          lastVolume = audio.volume;
          audio.muted = true;
          if (volumeSlider) volumeSlider.value = "0";
        }
        updateMuteIcon();
      });
    }

    // Track list
    trackButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        loadTrack(btn);
        audio.play().catch(() => setPlayingUI(false));
      });
    });

    // Initialize first track metadata if present
    const first = trackButtons.find((b) => b.classList.contains("is-active")) || trackButtons[0];
    if (first) loadTrack(first);

    updateMuteIcon();
  }

  /* ========================================================================
     Contact form — client-side validation + mailto fallback
     ======================================================================== */
  function initContactForm() {
    const form = $("#contact-form");
    if (!form) return;

    const success = $("#form-success");
    const fields = {
      name: $("#name", form),
      email: $("#email", form),
      subject: $("#subject", form),
      message: $("#message", form),
    };

    const emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    const setError = (field, msg) => {
      const group = field.closest(".form-group");
      if (!group) return;
      group.classList.add("has-error");
      const err = $(".form-error", group);
      if (err) err.textContent = msg;
      field.setAttribute("aria-invalid", "true");
    };

    const clearError = (field) => {
      const group = field.closest(".form-group");
      if (!group) return;
      group.classList.remove("has-error");
      field.removeAttribute("aria-invalid");
    };

    Object.values(fields).forEach((field) => {
      if (!field) return;
      field.addEventListener("input", () => clearError(field));
      field.addEventListener("blur", () => validateField(field));
    });

    function validateField(field) {
      const value = field.value.trim();
      if (field.hasAttribute("required") && !value) {
        setError(field, "This field is required.");
        return false;
      }
      if (field.type === "email" && value && !emailRe.test(value)) {
        setError(field, "Please enter a valid email address.");
        return false;
      }
      if (field.id === "message" && value.length > 0 && value.length < 10) {
        setError(field, "Message should be at least 10 characters.");
        return false;
      }
      clearError(field);
      return true;
    }

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      let ok = true;
      Object.values(fields).forEach((field) => {
        if (field && !validateField(field)) ok = false;
      });
      if (!ok) {
        const firstInvalid = form.querySelector("[aria-invalid='true']");
        if (firstInvalid) firstInvalid.focus();
        return;
      }

      const name = fields.name.value.trim();
      const email = fields.email.value.trim();
      const subject = fields.subject.value.trim() || "Message from LofiMellowHQ.studio";
      const message = fields.message.value.trim();

      // Static-site friendly: open mailto with composed body
      const body = [
        `Name: ${name}`,
        `Email: ${email}`,
        "",
        message,
        "",
        "— Sent via LofiMellowHQ.studio contact form",
      ].join("\n");

      const mailto = `mailto:hello@lofimellowhq.studio?subject=${encodeURIComponent(
        subject
      )}&body=${encodeURIComponent(body)}`;

      // Show success UI (works even if mailto is blocked)
      if (success) {
        success.classList.add("is-visible");
        success.setAttribute("role", "status");
      }

      form.reset();
      Object.values(fields).forEach((f) => f && clearError(f));

      // Attempt mailto open after brief feedback
      window.setTimeout(() => {
        window.location.href = mailto;
      }, 400);
    });
  }

  /* ========================================================================
     Current year in footer
     ======================================================================== */
  function initYear() {
    $$("[data-year]").forEach((el) => {
      el.textContent = String(new Date().getFullYear());
    });
  }

  /* ========================================================================
     Lazy-load enhancement (native + fallback)
     ======================================================================== */
  function initLazyImages() {
    $$("img[loading='lazy']").forEach((img) => {
      if ("loading" in HTMLImageElement.prototype) return;
      // Older browsers: load when near viewport
      if ("IntersectionObserver" in window) {
        const io = new IntersectionObserver((entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              const el = entry.target;
              if (el.dataset.src) el.src = el.dataset.src;
              io.unobserve(el);
            }
          });
        });
        io.observe(img);
      }
    });
  }

  /* ========================================================================
     Boot
     ======================================================================== */
  function init() {
    initHeader();
    initNav();
    initHeroAmbience();
    initReveal();
    initAudioPlayer();
    initContactForm();
    initYear();
    initLazyImages();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
