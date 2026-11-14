(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SwarmDurations = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  function split(seconds) {
    if (!Number.isSafeInteger(seconds) || seconds < 0) throw new Error("Invalid duration");
    return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60];
  }
  function total(values) {
    if (values.length !== 3 || values.some((value) => !/^\d+$/.test(String(value)))) return null;
    const [hours, minutes, seconds] = values.map(Number);
    const result = hours * 3600 + minutes * 60 + seconds;
    return Number.isSafeInteger(result) && minutes < 60 && seconds < 60 ? result : null;
  }
  function format(seconds) {
    const parts = split(seconds), units = ["hour", "minute", "second"];
    return parts.map((value, i) => value ? `${value} ${units[i]}${value === 1 ? "" : "s"}` : "").filter(Boolean).join(" ") || "0 seconds";
  }
  function editor(document, { name, label, value, minimum, maximum, locked }) {
    const group = document.createElement("fieldset");
    group.className = "duration-control";
    group.disabled = locked;
    const legend = document.createElement("legend");
    legend.className = "sr-only";
    legend.textContent = label;
    group.append(legend);
    const input = document.createElement("input");
    input.type = "hidden";
    input.id = "setting-" + name;
    input.name = name;
    input.disabled = locked;
    input.value = value;
    input.dataset.type = "number";
    input.dataset.original = JSON.stringify(value);
    const parts = split(value), controls = [];
    for (const [i, unit] of ["Hours", "Minutes", "Seconds"].entries()) {
      const field = document.createElement("label"), caption = document.createElement("span"), control = document.createElement("input");
      caption.textContent = unit;
      control.id = `setting-${name}-${unit.toLowerCase()}`;
      control.type = "number";
      control.inputMode = "numeric";
      control.autocomplete = "off";
      control.required = true;
      control.min = 0;
      control.max = i === 0 ? Math.floor(maximum / 3600) : 59;
      control.step = 1;
      control.value = parts[i];
      control.disabled = locked;
      control.setAttribute("aria-describedby", `duration-${name}-summary duration-${name}-range`);
      field.append(caption, control);
      controls.push(control);
      group.append(field);
    }
    const summary = document.createElement("span"), range = document.createElement("span");
    summary.id = `duration-${name}-summary`;
    summary.className = "duration-summary";
    summary.setAttribute("aria-live", "polite");
    range.id = `duration-${name}-range`;
    range.className = "duration-range";
    range.textContent = `Allowed: ${format(minimum)} to ${format(maximum)}.`;
    function update() {
      const seconds = total(controls.map((control) => control.value));
      const valid = seconds !== null && seconds >= minimum && seconds <= maximum;
      controls[0].setCustomValidity(valid ? "" : `Enter a duration from ${format(minimum)} to ${format(maximum)}. Use whole hours, and minutes and seconds from 0 to 59.`);
      input.value = valid ? seconds : "";
      summary.textContent = valid ? format(seconds) : "Enter a duration within the allowed range.";
      summary.classList.toggle("error", !valid);
    }
    controls.forEach((control) => control.addEventListener("input", update));
    update();
    group.append(input, summary, range);
    return { element: group, input, focusId: controls[0].id };
  }
  return { split, total, format, editor };
});
