// Invite request form: posts JSON to the Worker at /api/invite.
(() => {
  const form = document.querySelector(".invite-form");
  const status = form.querySelector(".invite-status");
  const button = form.querySelector("button");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    button.disabled = true;
    status.textContent = "Sending…";
    status.dataset.state = "";
    try {
      const res = await fetch("/api/invite", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || "The request did not go through. Try again.");
      form.reset();
      status.textContent = "Thank you. Your request is in, and we will write to you.";
      status.dataset.state = "ok";
    } catch (err) {
      status.textContent = err.message.startsWith("Failed to fetch") ? "No connection. Try again." : err.message;
      status.dataset.state = "error";
    } finally {
      button.disabled = false;
    }
  });
})();
