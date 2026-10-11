/* Served locally in place of every GeM / third-party script except jQuery.
 * We never download GeM's assets (robots.txt disallows /resources/), so the
 * plugins the page expects are replaced with harmless no-ops. The page only
 * needs jQuery to run its own search request; how it draws cards is irrelevant
 * because we read the search response, not the screen. */
(function () {
  var $ = window.jQuery;
  if ($ && $.fn) {
    var noop = function () { return this; };
    ["popover", "tooltip", "modal", "dropdown", "collapse", "tab", "datepicker",
     "autocomplete", "pagination", "fancybox", "smartmenus", "writtenNumber"].forEach(function (name) {
      if (!$.fn[name]) $.fn[name] = noop;
    });
    if (!$.datepicker) $.datepicker = { setDefaults: noop, formatDate: function () { return ""; } };
  }
  if (!window.bootbox) {
    window.bootbox = { alert: function () {}, confirm: function () {}, dialog: function () {}, hideAll: function () {} };
  }
  if (!window.Modernizr) window.Modernizr = {};
})();
