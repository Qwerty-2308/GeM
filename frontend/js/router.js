// Hash router. New pages are added by registering a route, not by editing
// the router (open for extension, closed for modification).

export class Router {
  constructor(container) {
    this.container = container;
    this.routes = [];
    window.addEventListener("hashchange", () => this.resolve());
  }

  // pattern like "/bids/:id"
  register(pattern, view) {
    const keys = [];
    const regex = new RegExp(
      "^" + pattern.replace(/:(\w+)/g, (_, key) => { keys.push(key); return "([^/]+)"; }) + "$",
    );
    this.routes.push({ regex, keys, view, name: pattern.split("/")[1] });
    return this;
  }

  async resolve() {
    const path = location.hash.replace(/^#/, "") || "/bids";
    for (const route of this.routes) {
      const match = path.match(route.regex);
      if (!match) continue;
      const params = Object.fromEntries(route.keys.map((k, i) => [k, decodeURIComponent(match[i + 1])]));
      this.#highlightNav(route.name);
      this.container.replaceChildren();
      await route.view(this.container, params);
      return;
    }
    location.hash = "#/bids";
  }

  #highlightNav(name) {
    document.querySelectorAll("[data-route]").forEach((a) => {
      a.classList.toggle("active", a.dataset.route === name);
    });
  }
}
