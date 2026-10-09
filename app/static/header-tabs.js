/* SAZ tab-aware header — hide cart on vehicles, show "Sell a car" there. */
(function () {
  function apply() {
    var activeTab = document.querySelector('button[role="tab"][aria-selected="true"][data-v]');
    var tab = activeTab ? activeTab.getAttribute("data-v") : "parts";
    var isVehicles = tab === "vehicles";

    var cart = document.getElementById("header-cart");
    var sellCar = document.getElementById("header-sell-car");

    if (cart) cart.style.display = isVehicles ? "none" : "";
    if (sellCar) sellCar.style.display = isVehicles ? "" : "none";
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Watch for tab clicks
    var tParts = document.getElementById("t-parts");
    var tVehicles = document.getElementById("t-vehicles");
    if (tParts) tParts.addEventListener("click", function () { setTimeout(apply, 0); });
    if (tVehicles) tVehicles.addEventListener("click", function () { setTimeout(apply, 0); });
    // Initial state
    apply();
  });
  // Also respond to custom events if the page fires them
  document.addEventListener("saz-tab-change", apply);
})();
