if (!reducedMotion && window.gsap && window.ScrollTrigger) {
  window.gsap.registerPlugin(window.ScrollTrigger);
  if (document.querySelector(".signal-ring")) {
    window.gsap.to(".signal-ring", { scale: 1.06, duration: 2.4, repeat: -1, yoyo: true, ease: "sine.inOut" });
  }
  window.gsap.utils.toArray(".portal-card img, .rank-character").forEach((image) => {
    window.gsap.to(image, { yPercent: -7, ease: "none", scrollTrigger: { trigger: image.parentElement, scrub: .7, start: "top bottom", end: "bottom top" } });
  });
}
