// FPEL Credit Check - small frontend helpers
//
// 1. Auto-calculate Total MWp = Solar + Wind*2 on the New Case form
// 2. Live company-name autocomplete that calls /api/companies/search
//    (mock Instafinancials lookup)
// 3. Footer location switcher (mirrors Site Survey)

(function () {
  // ---- Capacity calculator ----
  const solar = document.getElementById("solar");
  const wind = document.getElementById("wind");
  const total = document.getElementById("total");

  function recalc() {
    if (!total) return;
    const s = parseFloat(solar && solar.value) || 0;
    const w = parseFloat(wind && wind.value) || 0;
    total.value = (s + w * 2).toFixed(2);
  }
  if (solar) solar.addEventListener("input", recalc);
  if (wind) wind.addEventListener("input", recalc);
  recalc();

  // ---- Company autocomplete ----
  const nameInput = document.getElementById("company_name");
  const suggestList = document.getElementById("company-suggest");
  let debounceTimer;

  if (nameInput && suggestList) {
    nameInput.addEventListener("input", function () {
      const q = nameInput.value.trim();
      clearTimeout(debounceTimer);
      if (q.length < 2) {
        suggestList.classList.remove("show");
        return;
      }
      debounceTimer = setTimeout(function () {
        fetch("/api/companies/search?q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            suggestList.innerHTML = "";
            if (!data.length) {
              suggestList.classList.remove("show");
              return;
            }
            data.forEach(function (item) {
              const li = document.createElement("li");
              li.textContent = item.name + " (" + (item.industry || "-") + ")";
              li.addEventListener("click", function () {
                nameInput.value = item.name;
                suggestList.classList.remove("show");
              });
              suggestList.appendChild(li);
            });
            suggestList.classList.add("show");
          })
          .catch(function () { /* swallow */ });
      }, 200);
    });

    document.addEventListener("click", function (e) {
      if (e.target !== nameInput) suggestList.classList.remove("show");
    });
  }
})();

// ==========================================
// FOOTER — LOCATION SWITCHER
// (imported from site-survey/static/app.js for visual parity)
// ==========================================
function initFooter() {
  var pills     = document.querySelectorAll('.location-pill');
  var container = document.getElementById('footerAddressContainer');
  var addrText  = document.getElementById('addressText');
  var mapsLink  = document.getElementById('footerMapsLink');

  if (!pills.length) return;

  var locations = {
    hmt: {
      address: 'Fourth Partner House, Plot No N46, H No. 4-9-10, HMT Nagar, Nacharam, Hyderabad (TG), 500076',
      query:   'Fourth+Partner+House+Plot+No+N46+HMT+Nagar+Nacharam+Hyderabad+500076'
    },
    begumpet: {
      address: '11th floor, KURA TOWER, Motilal Nehru Nagar, Begumpet, Hyderabad (TG), 500016',
      query:   'KURA+TOWER+Motilal+Nehru+Nagar+Begumpet+Hyderabad+500016'
    },
    gurugram: {
      address: '3rd Floor, J-1/37, DLF City Ph-II, Gurugram (HR), 122002',
      query:   'DLF+City+Ph-II+Gurugram+122002'
    },
    noida: {
      address: '1st floor, Tower-B, The ITHUM Building, 109/110, Sector 62, Noida (UP), 201309',
      query:   'The+ITHUM+Building+Sector+62+Noida+201309'
    },
    chennai: {
      address: '2nd Floor, 19th Ave, Sector 10, Sector 13, Ashok Nagar, Chennai (TN), 600083',
      query:   'Ashok+Nagar+Chennai+600083'
    },
    mumbai: {
      address: '5th Floor, Indiana Business Centre, Makwana Rd, Marol Naka, Andheri East, Mumbai (MH), 400059',
      query:   'Indiana+Business+Centre+Marol+Naka+Andheri+East+Mumbai+400059'
    },
    pune: {
      address: '1, 5th Floor, Downtown City Vista, Ashoka Nagar, Kharadi, Pune (MH), 411014',
      query:   'Downtown+City+Vista+Kharadi+Pune+411014'
    }
  };

  pills.forEach(function (pill) {
    pill.addEventListener('click', function () {
      var id = pill.dataset.id;
      if (!locations[id]) return;

      pills.forEach(function (p) { p.classList.remove('active'); });
      pill.classList.add('active');

      if (container) container.classList.add('fade-out');

      setTimeout(function () {
        if (addrText) addrText.textContent = locations[id].address;
        if (mapsLink) mapsLink.href = 'https://www.google.com/maps/search/?api=1&query=' + locations[id].query;
        if (container) container.classList.remove('fade-out');
      }, 300);
    });
  });
}

document.addEventListener('DOMContentLoaded', function () {
  initFooter();
});
