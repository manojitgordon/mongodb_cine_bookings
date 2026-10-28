/**
 * static/js/app.js
 * -----------------
 * Frontend flow:
 * movies → shows → seats → payout → confirmation
 * Talks only to the FastAPI routes in app.py.
 */

const state = {
  city: "",
  movieId: null,
  movieTitle: "",
  showId: null,
  showMeta: null,
  seatsData: null,
  selected: new Set(),
  holdId: null,
  heldUntil: null,
  holdTimer: null,
};

const els = {
  movieGrid: document.getElementById("movieGrid"),
  cityFilter: document.getElementById("cityFilter"),
  showCityFilter: document.getElementById("showCityFilter"),
  showDateFilter: document.getElementById("showDateFilter"),
  showsTitle: document.getElementById("showsTitle"),
  showList: document.getElementById("showList"),
  seatsTitle: document.getElementById("seatsTitle"),
  seatsMeta: document.getElementById("seatsMeta"),
  seatMap: document.getElementById("seatMap"),
  priceNote: document.getElementById("priceNote"),
  selectedSeatsLabel: document.getElementById("selectedSeatsLabel"),
  selectedAmount: document.getElementById("selectedAmount"),
  goPayoutBtn: document.getElementById("goPayoutBtn"),
  payMovie: document.getElementById("payMovie"),
  payTheater: document.getElementById("payTheater"),
  payTime: document.getElementById("payTime"),
  paySeats: document.getElementById("paySeats"),
  payBooking: document.getElementById("payBooking"),
  payStatus: document.getElementById("payStatus"),
  payAmount: document.getElementById("payAmount"),
  payCity: document.getElementById("payCity"),
  mobileInput: document.getElementById("mobileInput"),
  payBtn: document.getElementById("payBtn"),
  payoutError: document.getElementById("payoutError"),
  ticketCard: document.getElementById("ticketCard"),
  toast: document.getElementById("toast"),
  cityHint: document.getElementById("cityHint"),
  bookingsPhone: document.getElementById("bookingsPhone"),
  bookingsBody: document.getElementById("bookingsBody"),
  bookingsEmpty: document.getElementById("bookingsEmpty"),
  bookingsHint: document.getElementById("bookingsHint"),
  holdCountdown: document.getElementById("holdCountdown"),
  holdBanner: document.getElementById("holdBanner"),
};

// ------------------------------------------------------------
// Tiny helpers
// ------------------------------------------------------------

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail;
    const message = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
        : `Request failed (${res.status})`;
    const err = new Error(message);
    err.status = res.status;
    throw err;
  }
  return data;
}

function showStep(name) {
  document.querySelectorAll(".step").forEach((s) => s.classList.remove("active"));
  document.getElementById(`step-${name}`).classList.add("active");
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { els.toast.hidden = true; }, 3200);
}

function formatMoney(n) {
  return `₹${Number(n).toLocaleString("en-IN")}`;
}

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** City must be chosen before browsing / booking. */
function requireCity() {
  const city = els.cityFilter.value.trim();
  if (!city) {
    toast("Please select a city first");
    return null;
  }
  return city;
}

/**
 * Phone rules (same as backend):
 * - digits only
 * - length between 6 and 10 (inclusive)
 */
function validatePhone(value) {
  const phone = String(value || "").trim();
  if (!/^\d+$/.test(phone)) {
    return { ok: false, message: "Phone number must contain digits only" };
  }
  if (phone.length < 6 || phone.length > 10) {
    return { ok: false, message: "Phone number must be between 6 and 10 digits" };
  }
  return { ok: true, phone };
}

// ------------------------------------------------------------
// Step 1 — movies
// ------------------------------------------------------------

async function loadMovies() {
  const city = els.cityFilter.value.trim();
  state.city = city;

  if (!city) {
    els.movieGrid.innerHTML = `<p class="empty">Select a city to see movies.</p>`;
    if (els.cityHint) els.cityHint.hidden = false;
    return;
  }
  if (els.cityHint) els.cityHint.hidden = true;

  const qs = `?city=${encodeURIComponent(city)}`;
  const data = await api(`/api/movies${qs}`);

  if (!data.movies.length) {
    els.movieGrid.innerHTML = `<p class="empty">No movies for this city.</p>`;
    return;
  }

  els.movieGrid.innerHTML = data.movies.map((m) => `
    <article class="movie-card" data-movie-id="${m._id}" data-title="${m.title}">
      <h2>${m.title}</h2>
      <div class="meta">${m.language} · ${m.duration_min} min · ${m.certification}</div>
      <div class="chip-row">
        ${(m.genres || []).map((g) => `<span class="chip">${g}</span>`).join("")}
      </div>
      <div class="chip-row">
        ${(m.cities || []).map((c) => `<span class="chip">${c}</span>`).join("")}
      </div>
    </article>
  `).join("");
}

// ------------------------------------------------------------
// Step 2 — shows
// ------------------------------------------------------------

async function loadShows() {
  const city = els.showCityFilter.value.trim() || state.city;
  if (!city) {
    els.showList.innerHTML = `<p class="empty">Select a city to see shows.</p>`;
    toast("Please select a city");
    return;
  }
  state.city = city;
  els.showCityFilter.value = city;

  const params = new URLSearchParams();
  params.set("city", city);
  const date = els.showDateFilter.value;
  if (date) params.set("date", date);

  const data = await api(`/api/movies/${state.movieId}/shows?${params}`);

  els.showsTitle.textContent = data.movie?.title || "Shows";

  if (!data.shows.length) {
    els.showList.innerHTML = `<p class="empty">No shows for this city/date. Try another date.</p>`;
    return;
  }

  els.showList.innerHTML = data.shows.map((s) => `
    <article class="show-item" data-show-id="${s._id}">
      <div>
        <div class="time">${formatTime(s.start_time)}</div>
        <div class="meta">${s.theater_name} · ${s.city} · Screen ${s.screen_id}</div>
        <div class="meta">Premium ₹${s.prices.PREMIUM} · Regular ₹${s.prices.REGULAR}</div>
      </div>
      <div class="meta">${s.available_seats}/${s.total_seats} free</div>
    </article>
  `).join("");
}

// ------------------------------------------------------------
// Step 3 — seats
// ------------------------------------------------------------

async function loadSeats(showId) {
  state.showId = showId;
  state.selected.clear();

  const data = await api(`/api/shows/${showId}/seats`);
  state.seatsData = data;
  state.showMeta = data;

  els.seatsTitle.textContent = data.movie_title;
  els.seatsMeta.textContent = `${data.theater_name} · ${data.city} · ${formatTime(data.start_time)}`;
  els.priceNote.textContent = `Premium ₹${data.prices.PREMIUM} · Regular ₹${data.prices.REGULAR}`;

  // Group seats by row letter (A1 -> row A)
  const byRow = {};
  for (const seat of data.seats) {
    const row = seat.seat[0];
    (byRow[row] ||= []).push(seat);
  }

  els.seatMap.innerHTML = Object.keys(byRow).sort().map((row) => {
    const seats = byRow[row]
      .sort((a, b) => parseInt(a.seat.slice(1), 10) - parseInt(b.seat.slice(1), 10))
      .map((s) => {
        const booked = s.status === "BOOKED";
        const held = s.status === "HELD";
        const locked = booked || held;
        const cls = booked ? "booked" : held ? "held" : "";
        const tip = held
          ? `${s.seat} · held until payment / expiry`
          : `${s.seat} · ${s.category}`;
        return `<button
          class="seat ${cls}"
          data-seat="${s.seat}"
          data-category="${s.category}"
          ${locked ? "disabled" : ""}
          title="${tip}"
        >${s.seat.slice(1)}</button>`;
      }).join("");
    return `<div class="seat-row"><span class="row-label">${row}</span>${seats}</div>`;
  }).join("");

  updateSelectionSummary();
  showStep("seats");
}

function seatPrice(seatLabel) {
  const seat = state.seatsData.seats.find((s) => s.seat === seatLabel);
  return state.seatsData.prices[seat.category];
}

function updateSelectionSummary() {
  const seats = [...state.selected];
  const amount = seats.reduce((sum, s) => sum + seatPrice(s), 0);
  els.selectedSeatsLabel.textContent = seats.length ? seats.join(", ") : "None";
  els.selectedAmount.textContent = formatMoney(amount);
  els.goPayoutBtn.disabled = seats.length === 0;
}

// ------------------------------------------------------------
// Step 4 — hold seats, then payout
// ------------------------------------------------------------

function stopHoldTimer() {
  if (state.holdTimer) {
    clearInterval(state.holdTimer);
    state.holdTimer = null;
  }
}

function formatCountdown(msLeft) {
  const totalSec = Math.max(0, Math.ceil(msLeft / 1000));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function startHoldTimer(heldUntilIso) {
  stopHoldTimer();
  state.heldUntil = new Date(heldUntilIso);
  els.holdBanner.classList.remove("expired");

  const tick = async () => {
    const msLeft = state.heldUntil.getTime() - Date.now();
    els.holdCountdown.textContent = formatCountdown(msLeft);
    if (msLeft <= 0) {
      stopHoldTimer();
      els.holdBanner.classList.add("expired");
      els.holdCountdown.textContent = "0:00";
      els.payBtn.disabled = true;
      state.holdId = null;
      toast("Hold expired — booking is EXPIRED and seats were released.");
      await loadSeats(state.showId);
    }
  };

  tick();
  state.holdTimer = setInterval(tick, 1000);
}

async function releaseCurrentHold() {
  if (!state.holdId || !state.showId) {
    stopHoldTimer();
    return;
  }
  const seats = [...state.selected].join(",");
  const holdId = state.holdId;
  state.holdId = null;
  stopHoldTimer();
  try {
    await api(
      `/api/holds/${encodeURIComponent(holdId)}/release?show_id=${encodeURIComponent(state.showId)}&seats=${encodeURIComponent(seats)}`,
      { method: "POST" }
    );
  } catch (_) {
    // Hold may already have expired — ignore
  }
}

async function openPayout() {
  if (!requireCity()) return;
  if (!state.selected.size) return;

  els.goPayoutBtn.disabled = true;
  els.payoutError.hidden = true;

  try {
    // Lock seats as HELD for 5 minutes before payment
    const hold = await api("/api/holds", {
      method: "POST",
      body: JSON.stringify({
        show_id: state.showId,
        seats: [...state.selected],
        city: state.city,
      }),
    });

    state.holdId = hold.hold_id;
    const m = state.showMeta;

    els.payMovie.textContent = m.movie_title;
    els.payTheater.textContent = `${m.theater_name} · ${m.city}`;
    els.payTime.textContent = formatTime(m.start_time);
    els.paySeats.textContent = hold.seats.join(", ");
    els.payBooking.textContent = hold.booking_id;
    els.payStatus.textContent = hold.booking_status || "AWAITING_PAYMENT";
    els.payAmount.textContent = formatMoney(hold.amount);
    els.payCity.value = state.city || m.city;
    els.payoutError.hidden = true;
    els.payoutError.textContent = "";
    els.payBtn.disabled = false;

    startHoldTimer(hold.held_until);
    showStep("payout");
    toast(`Booking ${hold.booking_id} is awaiting payment for ${hold.hold_minutes} minutes`);
  } catch (err) {
    toast(err.message);
    // Refresh map — someone else may have taken a seat
    await loadSeats(state.showId);
  } finally {
    els.goPayoutBtn.disabled = state.selected.size === 0;
  }
}

async function payAndBook() {
  els.payoutError.hidden = true;

  if (!state.holdId) {
    els.payoutError.textContent = "Seat hold expired. Go back and select seats again.";
    els.payoutError.hidden = false;
    return;
  }

  const city = (state.city || els.payCity.value || "").trim();
  if (!city) {
    els.payoutError.textContent = "City is required — go back and select a city";
    els.payoutError.hidden = false;
    return;
  }

  const phoneCheck = validatePhone(els.mobileInput.value);
  if (!phoneCheck.ok) {
    els.payoutError.textContent = phoneCheck.message;
    els.payoutError.hidden = false;
    return;
  }

  els.payBtn.disabled = true;

  try {
    const body = {
      show_id: state.showId,
      seats: [...state.selected],
      city,
      mobile_number: phoneCheck.phone,
      hold_id: state.holdId,
    };
    const data = await api("/api/bookings", {
      method: "POST",
      body: JSON.stringify(body),
    });

    stopHoldTimer();
    state.holdId = null;

    const b = data.booking;
    els.ticketCard.innerHTML = `
      <h2>Booked</h2>
      <div class="row"><span>Booking ID</span><strong>${b._id}</strong></div>
      <div class="row"><span>City</span><strong>${city}</strong></div>
      <div class="row"><span>Phone</span><strong>${b.mobile_number}</strong></div>
      <div class="row"><span>Seats</span><strong>${b.seats.join(", ")}</strong></div>
      <div class="row"><span>Current status</span><strong>${b.status}</strong></div>
      <div class="row"><span>Payment</span><strong>${b.payment_status}</strong></div>
      <div class="row total"><span>Paid</span><strong>${formatMoney(b.amount)}</strong></div>
    `;
    if (els.bookingsPhone) els.bookingsPhone.value = b.mobile_number;
    showStep("done");
    toast("Payment received — booking is BOOKED");
  } catch (err) {
    els.payoutError.textContent = err.message;
    els.payoutError.hidden = false;
    if (err.status === 409) {
      stopHoldTimer();
      state.holdId = null;
      toast(err.message);
      await loadSeats(state.showId);
    } else {
      els.payBtn.disabled = false;
    }
  }
}

// ------------------------------------------------------------
// Step 6 — view / cancel bookings
// ------------------------------------------------------------

function formatHistory(history) {
  const items = Array.isArray(history) ? history : [];
  if (!items.length) return `<span class="muted">—</span>`;
  return `<ul class="history-list">${items.map((h) => {
    const when = h.at ? formatTime(h.at) : "";
    return `<li><strong>${h.status}</strong> · ${when}</li>`;
  }).join("")}</ul>`;
}

function statusPill(status) {
  const cls = {
    BOOKED: "booked",
    AWAITING_PAYMENT: "awaiting",
    EXPIRED: "expired",
    CANCELLED: "cancelled",
  }[status] || "expired";
  return `<span class="status-pill ${cls}">${status}</span>`;
}

function renderBookings(bookings) {
  if (!bookings.length) {
    els.bookingsBody.innerHTML = "";
    els.bookingsEmpty.hidden = false;
    return;
  }
  els.bookingsEmpty.hidden = true;
  els.bookingsBody.innerHTML = bookings.map((b) => {
    const current = b.status || (b.status_history && b.status_history.length
      ? b.status_history[b.status_history.length - 1].status
      : "");
    const canCancel = current === "BOOKED";
    return `
      <tr data-booking-id="${b._id}">
        <td><strong>${b._id}</strong><div class="muted small">${b.city || ""}</div></td>
        <td>${b.mobile_number || "—"}</td>
        <td>
          <div>${b.show_id}</div>
          <div class="muted small">${(b.seats || []).join(", ")}</div>
        </td>
        <td>${formatMoney(b.amount)}</td>
        <td>${statusPill(current)}</td>
        <td>${formatHistory(b.status_history)}</td>
        <td>
          <button
            class="btn danger cancel-btn"
            data-cancel-id="${b._id}"
            ${canCancel ? "" : "disabled"}
            title="${canCancel ? "Cancel booking" : "Only a BOOKED booking can be cancelled"}"
          >${canCancel ? "Cancel" : "Locked"}</button>
        </td>
      </tr>
    `;
  }).join("");
}

async function loadBookings(phone) {
  const qs = phone ? `?mobile_number=${encodeURIComponent(phone)}` : "";
  const data = await api(`/api/bookings${qs}`);
  renderBookings(data.bookings || []);
  els.bookingsHint.textContent = phone
    ? `Showing bookings for ${phone}`
    : "Showing recent bookings";
}

async function openBookingsView(preferPhone) {
  showStep("bookings");
  if (preferPhone) els.bookingsPhone.value = preferPhone;
  const phone = els.bookingsPhone.value.trim();
  if (phone) {
    const check = validatePhone(phone);
    if (check.ok) {
      await loadBookings(check.phone);
      return;
    }
  }
  await loadBookings(null);
}

async function cancelBooking(bookingId) {
  if (!confirm(`Cancel booking ${bookingId}? This cannot be undone.`)) return;
  await api(`/api/bookings/${bookingId}/cancel`, { method: "POST" });
  toast("Booking cancelled — seats released");
  const phone = els.bookingsPhone.value.trim();
  const check = phone ? validatePhone(phone) : { ok: false };
  await loadBookings(check.ok ? check.phone : null);
}

// ------------------------------------------------------------
// Step 7 — analytics (Part D)
// ------------------------------------------------------------

function emptyRow(cols, msg = "No data yet") {
  return `<tr><td colspan="${cols}" class="muted">${msg}</td></tr>`;
}

async function loadAnalytics() {
  const data = await api("/api/analytics/summary");

  const revenue = data.revenue_by_movie || [];
  document.getElementById("analyticsRevenue").innerHTML = revenue.length
    ? revenue.map((r) => `
        <tr>
          <td>${r.title || r.movie_id}</td>
          <td>${r.tickets_sold}</td>
          <td>${r.bookings}</td>
          <td>${formatMoney(r.revenue)}</td>
        </tr>`).join("")
    : emptyRow(4);

  const top = data.top_movies || [];
  document.getElementById("analyticsTop").innerHTML = top.length
    ? top.map((r, i) => `
        <tr>
          <td>#${i + 1}</td>
          <td>${r.title || r.movie_id}</td>
          <td>${r.tickets_sold}</td>
          <td>${formatMoney(r.revenue)}</td>
        </tr>`).join("")
    : emptyRow(4);

  const occ = data.occupancy_by_show || [];
  document.getElementById("analyticsOccupancy").innerHTML = occ.length
    ? occ.map((r) => `
        <tr>
          <td>${r.show_id}</td>
          <td>${r.title || r.movie_id}</td>
          <td>${r.city || ""}</td>
          <td>${r.booked_seats}/${r.total_seats}</td>
          <td>${r.occupancy_pct}%</td>
        </tr>`).join("")
    : emptyRow(5);

  const peak = data.peak_time_slots || [];
  document.getElementById("analyticsPeak").innerHTML = peak.length
    ? peak.map((r) => `
        <tr>
          <td>${r.hour_label || r.hour}</td>
          <td>${r.bookings}</td>
          <td>${r.tickets_sold}</td>
          <td>${formatMoney(r.revenue)}</td>
        </tr>`).join("")
    : emptyRow(4, "No paid bookings yet — complete a payment first");
}

async function openAnalytics() {
  showStep("analytics");
  await loadAnalytics();
}

// ------------------------------------------------------------
// Events
// ------------------------------------------------------------

els.cityFilter.addEventListener("change", () => {
  loadMovies().catch((e) => toast(e.message));
});

els.movieGrid.addEventListener("click", (e) => {
  const card = e.target.closest("[data-movie-id]");
  if (!card) return;
  if (!requireCity()) return;
  state.movieId = card.dataset.movieId;
  state.movieTitle = card.dataset.title;
  els.showCityFilter.value = state.city;
  loadShows().then(() => showStep("shows")).catch((err) => toast(err.message));
});

els.showCityFilter.addEventListener("change", () => {
  state.city = els.showCityFilter.value.trim();
  els.cityFilter.value = state.city;
  loadShows().catch((e) => toast(e.message));
});
els.showDateFilter.addEventListener("change", () => {
  loadShows().catch((e) => toast(e.message));
});

els.showList.addEventListener("click", (e) => {
  const item = e.target.closest("[data-show-id]");
  if (!item) return;
  if (!requireCity()) return;
  loadSeats(item.dataset.showId).catch((err) => toast(err.message));
});

els.seatMap.addEventListener("click", (e) => {
  const btn = e.target.closest(".seat");
  if (!btn || btn.disabled) return;
  const seat = btn.dataset.seat;
  if (state.selected.has(seat)) {
    state.selected.delete(seat);
    btn.classList.remove("selected");
  } else {
    state.selected.add(seat);
    btn.classList.add("selected");
  }
  updateSelectionSummary();
});

els.goPayoutBtn.addEventListener("click", () => {
  openPayout().catch((e) => toast(e.message));
});
els.payBtn.addEventListener("click", () => {
  payAndBook().catch((e) => toast(e.message));
});

document.getElementById("backFromPayout").addEventListener("click", () => {
  releaseCurrentHold()
    .then(() => loadSeats(state.showId))
    .catch((e) => toast(e.message));
});

// Live-filter phone input: strip non-digits and cap at 10 chars
els.mobileInput.addEventListener("input", () => {
  const digits = els.mobileInput.value.replace(/\D/g, "").slice(0, 10);
  if (els.mobileInput.value !== digits) {
    els.mobileInput.value = digits;
  }
});

els.bookingsPhone.addEventListener("input", () => {
  const digits = els.bookingsPhone.value.replace(/\D/g, "").slice(0, 10);
  if (els.bookingsPhone.value !== digits) {
    els.bookingsPhone.value = digits;
  }
});

document.getElementById("loadBookingsBtn").addEventListener("click", () => {
  const check = validatePhone(els.bookingsPhone.value);
  if (!check.ok) {
    toast(check.message);
    return;
  }
  loadBookings(check.phone).catch((e) => toast(e.message));
});

document.getElementById("loadAllBookingsBtn").addEventListener("click", () => {
  loadBookings(null).catch((e) => toast(e.message));
});

els.bookingsBody.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-cancel-id]");
  if (!btn || btn.disabled) return;
  cancelBooking(btn.dataset.cancelId).catch((err) => toast(err.message));
});

document.getElementById("navBookings").addEventListener("click", () => {
  openBookingsView(els.mobileInput.value.trim() || els.bookingsPhone.value.trim())
    .catch((e) => toast(e.message));
});

document.getElementById("navAnalytics").addEventListener("click", () => {
  openAnalytics().catch((e) => toast(e.message));
});

document.getElementById("refreshAnalyticsBtn").addEventListener("click", () => {
  loadAnalytics().catch((e) => toast(e.message));
});

document.getElementById("backFromAnalytics").addEventListener("click", () => {
  showStep("movies");
});

document.getElementById("navBook").addEventListener("click", () => {
  showStep("movies");
});

document.getElementById("viewBookingsBtn").addEventListener("click", () => {
  openBookingsView(els.bookingsPhone.value.trim() || els.mobileInput.value.trim())
    .catch((e) => toast(e.message));
});

document.getElementById("backFromBookings").addEventListener("click", () => {
  showStep("movies");
});

document.getElementById("bookAnotherBtn").addEventListener("click", () => {
  state.selected.clear();
  state.showId = null;
  showStep("movies");
  loadMovies().catch((e) => toast(e.message));
});

document.getElementById("brandHome").addEventListener("click", () => {
  showStep("movies");
});

document.querySelectorAll("[data-back]").forEach((btn) => {
  btn.addEventListener("click", () => showStep(btn.dataset.back));
});

// Boot — wait for city selection (no movies until city is chosen)
loadMovies().catch((e) => toast(e.message));
