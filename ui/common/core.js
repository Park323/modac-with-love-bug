window.Lovebug = (() => {
  const bridge = window.LovebugBridge || {};

  function qs(selector, root = document) {
    return root.querySelector(selector);
  }

  function setText(selector, value) {
    const target = qs(selector);
    if (target) target.textContent = value;
  }

  function formatTime(date = new Date()) {
    return date.toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit"
    });
  }

  function initStepper(root = document) {
    root.querySelectorAll("[data-stepper]").forEach((stepper) => {
      const input = qs("input", stepper);
      const minus = qs("[data-step='down']", stepper);
      const plus = qs("[data-step='up']", stepper);
      const min = Number(input.min || 1);
      const max = Number(input.max || 999);

      const clamp = (value) => Math.min(max, Math.max(min, Number(value) || min));

      minus.addEventListener("click", () => {
        input.value = clamp(Number(input.value) - 1);
        input.dispatchEvent(new Event("change"));
      });

      plus.addEventListener("click", () => {
        input.value = clamp(Number(input.value) + 1);
        input.dispatchEvent(new Event("change"));
      });

      input.addEventListener("change", () => {
        input.value = clamp(input.value);
      });
    });
  }

  return {
    bridge,
    formatTime,
    initStepper,
    qs,
    setText
  };
})();
