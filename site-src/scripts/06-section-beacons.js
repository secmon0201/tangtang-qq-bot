document.querySelectorAll("main > section").forEach((section) => {
  if (section.querySelector(":scope > .section-beacon")) return;
  const beacon = document.createElement("span");
  beacon.className = "section-beacon";
  beacon.setAttribute("aria-hidden", "true");
  section.append(beacon);
});
