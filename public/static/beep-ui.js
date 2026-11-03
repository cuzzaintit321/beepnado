(() => {
  const features = [
    ["Uptime checks",
     "Monitor authorized public HTTP and HTTPS websites. View actual response status and latency inside the app."],
    ["Incident timeline",
     "Track confirmed outages and recoveries with a recorded incident history."],
    ["Shareable status pages",
     "Publish the current status of selected monitored websites."],
    ["SSL certificate visibility",
     "See observed certificate expiration dates. Certificate renewal is not automated."],
    ["Faster paid checks",
     "One-minute primary checks are planned for paid plans. Billing is not active yet."],
    ["Outage & recovery feed",
     "See recorded downtime and recovery events inside your private dashboard."]
  ];

  const plans = [
    ["Free", "2 monitors, 5-minute checks, incident history and a status page. Public registration is not yet enabled."],
    ["Pro", "Proposed $12/month plan with 20 monitors and 1-minute primary checks. Checkout is not active."],
    ["Team", "Proposed $39/month plan with 100 monitors and 1-minute primary checks. Checkout is not active."]
  ];

  const dialog = document.createElement("dialog");
  dialog.className = "beep-dialog";
  dialog.innerHTML =
    '<h2></h2><p></p><button type="button">Close</button>';
  document.body.appendChild(dialog);

  let opener = null;
  function show(data) {
    opener = document.activeElement;
    dialog.querySelector("h2").textContent = data[0];
    dialog.querySelector("p").textContent = data[1];
    dialog.showModal();
  }

  dialog.querySelector("button").onclick = () => dialog.close();
  dialog.addEventListener("close", () => opener?.focus?.());

  function activate(cards, data) {
    document.querySelectorAll(cards).forEach((card, i) => {
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", "Learn more about " + data[i][0]);

      card.addEventListener("click", () => show(data[i]));
      card.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          show(data[i]);
        }
      });
    });
  }

  activate(".feature-card", features);
  activate(".price-card", plans);

  const demo = document.createElement("section");
  demo.id = "demo";
  demo.className = "beep-demo";
  demo.innerHTML = `
    <h2>See BeepNado in action.</h2>
    <p>Interactive monitoring illustration — not live website data.</p>
    <div>
      <button class="active" data-state="up">● Online</button>
      <button data-state="down">● Downtime</button>
      <button data-state="recovered">● Recovered</button>
    </div>
    <div class="beep-preview" aria-live="polite">
      <p>SIMULATED WEBSITE: your-site.example</p>
      <strong id="demo-state">ONLINE</strong>
      <p id="demo-message">HTTP 200 · 218 ms · No incidents</p>
    </div>
  `;

  document.querySelector("#features")?.after(demo);

  const examples = {
    up: ["ONLINE", "HTTP 200 · 218 ms · No incidents", "#7de9b7"],
    down: ["DOWN", "Timeout · Confirmed outage opened", "#ff7b89"],
    recovered: ["RECOVERED", "HTTP 200 · Earlier incident resolved", "#ffcb82"]
  };

  demo.querySelectorAll("[data-state]").forEach(button => {
    button.onclick = () => {
      const d = examples[button.dataset.state];
      demo.querySelector("#demo-state").textContent = d[0];
      demo.querySelector("#demo-state").style.color = d[2];
      demo.querySelector("#demo-message").textContent = d[1];
      demo.querySelectorAll("[data-state]").forEach(b => {
        b.classList.toggle("active", b === button);
      });
    };
  });

  const faq = document.createElement("section");
  faq.className = "beep-faq";
  faq.innerHTML = `
    <h2>Frequently asked questions</h2>
    <details>
      <summary>Does the interactive demo check real websites?</summary>
      <p>No. It illustrates monitoring states using sample data.</p>
    </details>
    <details>
      <summary>Can customers subscribe today?</summary>
      <p>Not yet. Public accounts and paid billing require
      further security and operational preparation.</p>
    </details>
    <details>
      <summary>Does BeepNado automatically repair outages?</summary>
      <p>No. It records outages and recovery; it does not repair them.</p>
    </details>
  `;

  document.querySelector("#pricing")?.after(faq);
})();
