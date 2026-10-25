# stk Frontend Reference

Canonical lookup for Vuetify/Vue component surface actually used in this codebase.
Every prop and pattern below is sourced from sealbook/templates/ or sealbook/static/js/ — not from memory.

## Pinned Versions

Authoritative list: `sealbook/static/VERSIONS.txt`. Bump a version in `vendor.sh`, run
`./vendor.sh`, then `uv run stk smoke`.

- **Vue**: 3.5.40 production build (`sealbook/static/js/vue.min.js`)
- **Vuetify**: 4.1.6 (`sealbook/static/js/vuetify.min.js` + `sealbook/static/css/vuetify.min.css`)
- **Axios**: 1.18.1 (`sealbook/static/js/axios.min.js`)
- **Tabler Icons**: 3.45.0, self-hosted (`sealbook/static/icons/`), woff2 only

## Typography (Material Design 3)

Vuetify 4 removed the Material Design 2 type classes. Use the MD3 names; the old
ones apply no styling at all and produce no console error, so nothing will warn you.

| Do not use (v3) | Use (v4)              | Size     |
|-----------------|-----------------------|----------|
| `text-h2`       | `text-display-large`  | 3.5625rem |
| `text-h4`       | `text-headline-large` | 2rem     |
| `text-h5`       | `text-headline-small` | 1.5rem   |
| `text-h6`       | `text-title-large`    | 1.375rem |
| `text-subtitle-1` | `text-title-medium` | 1rem     |
| `text-subtitle-2` | `text-title-small`  | .875rem  |
| `text-body-1`   | `text-body-large`     | 1rem     |
| `text-body-2`   | `text-body-medium`    | .875rem  |
| `text-caption`  | `text-body-small`     | .75rem   |
| `text-overline` | `text-label-small`    | .6875rem |

The font-family overrides in `sealbook/static/css/app.css` select on the MD3 names, so a
stray MD2 class also loses the Plus Jakarta Sans / Inter treatment.

Never reintroduce the Vue development build. Vue 3.5's production reactivity loops
forever on state mutated inside a computed, where the dev build only warns; that is
what `filterNavByRole` in `sealbook/static/js/components/index.js` must stay pure for.

## Boilerplate: App Init

Every page script follows this exact shape:

```html
{% block js %}
<script>
const {createApp, toRaw} = Vue;
const {createVuetify} = Vuetify;
const vuetify = createVuetify(config.vuetifyConfig);  // config in static/js/config.js

window.app = createApp({
  mixins: [layoutMixin],          // defined in layout.html <script>
  delimiters: config.delimiters,  // ['${', '}'] — avoids Jinja {{ conflict
  data() { return { ... }; },
  methods: { ... }
});

registerStkComponents(app);       // static/js/components/index.js
app.use(vuetify).mount("#app");
</script>
{% endblock %}
```

## Delimiters

Vue delimiter config: `['${', '}']`. In templates use `${expr}`, never `{{expr}}`.

```html
${item.name}
${JSON.stringify(currentData, null, 2)}
```

## Server Data via script tag

Pass Jinja data to Vue without XSS risk:

```html
<!-- In {% block js %}, outside the #app element -->
<script type="application/json" id="roles-data">
{{ roles|tojson|safe }}
</script>

<!-- In Vue data() -->
roles: JSON.parse(document.querySelector('#roles-data').textContent)
```

Vue strips script elements inside its mount element. Keep boot JSON in the Jinja
`js` block or the element lookup will return null.

## Axios Mutation Convention

Frontend always wraps mutation payloads in `{item: {...}}`. Backend extracts with `json_data.get("item", {})`.

```javascript
// create
axios.post('/api/thing/', {item: this.eitem})
// update
axios.post(`/api/thing/${this.eitem.id}`, {item: this.eitem})
// delete
axios.delete(`/api/thing/${item.id}`)
// list (GET with query params)
axios.get(`/api/things?page=${this.options.page}&per_page=${this.options.itemsPerPage}`)
```

## layoutMixin Contract

Defined inline in `sealbook/templates/layout.html`, together with the shell markup (top bar,
rail, hint bar, palette, help) and its CSS in `static/css/shell.css`. Page data wins over
mixin data on a name clash, so the mixin keeps distinct names.

**data:** `navItems` (the extensions' `app.navigation` entries, via `rail_navigation()`), `notes`, `notesNext`, `bellOpen`, `moreOpen`, `navCounts`
(`/api/nav/counts`), `caseSel` and `caseList` (case switcher, `/api/monitor/investigations`),
`shellQ`, `palOpen`, `palQ`, `palI`, `palRecords`, `shellHelp`, `isDark`, `shellHints`, `shellKeys`.

**computed:** `isMobile` (`$vuetify.display.smAndDown`), `railItems` (role-filtered, with
`current` and `count`), `caseOptions`, `caseLabel`, `palAll`, `palHits`.

**page hooks (optional):**
- `useCase(value)`: called when the switcher changes; `value` is an investigation or null
  for all. A page that scopes by investigation also keeps `caseSel` current.
- `toggleHelp()`: a page with its own keyboard help owns `?`; the shell leaves it alone.

**keys:** `/` focuses search (`/search?q=`), Cmd/Ctrl-K opens the palette, `?` opens help.
None fire while a text input, textarea, select or contenteditable has focus.

**methods:** `switchCase`, `setTheme`, `toggleTheme` (localStorage `stk-theme`),
`openHelp`, `openPalette`, `palKey`, `logout`, `_connectWs()` and `_onWsMessage(msg)`
(a page may extend it and call `layoutMixin.methods._onWsMessage`).

**bell:** opening it loads `GET /api/notifications?unread=1&limit=20`, older pages with
`before=<next>`; the badge is `navCounts.notifications` from `/api/nav/counts`. Opening an
item posts `{item: {ids: [id]}}` to `/api/notifications/read`, "Mark all read" posts
`{item: {all: true}}`; both return the new unread count. A live `notification` frame is
prepended and counted.

**mobile:** the bottom bar shows the first four rail items and More, a bottom sheet with
the rest and Add evidence.

---|---|
| VTextField | `variant="outlined"` |
| VSelect | `variant="outlined"` |
| VTextarea | `variant="outlined"` |
| VCombobox | `variant="outlined"` |
| VChip | `size="small"`, `rounded="sm"` |
| VCard | `elevation="0"`, `rounded="0"` |
| VMenu | `offset="10"` |
| VBtn | `variant="elevated"`, `size="small"`, `rounded="0"` |
| VDialog | `rounded="0"` |
| VToolbar | `elevation="0"` |
| VDataTableServer | `items-per-page="25"`, `items-per-page-options="[25, 50, 100]"` |

---

### v-data-table-server

Server-paginated table. Fires `@update:options` when page/sort changes — pass handler as `refresh`.

```html
<v-data-table-server
  :items="items"
  :items-length="itemsLength"
  :headers="headers"
  :page="options.page"
  :items-per-page="options.itemsPerPage"
  @update:options="refresh"
  hover
>
  <!-- Toolbar slot for action buttons -->
  <template v-slot:top>
    <v-toolbar dense elevation="0" color="transparent" class="mb-4">
      <v-btn class="ml-auto" @click="createItem" size="small" color="primary" variant="elevated">
        <template v-slot:prepend><i class="ti ti-plus"></i></template>
        Add Item
      </v-btn>
    </v-toolbar>
  </template>

  <!-- Custom cell -->
  <template v-slot:item.roles="{ item }">
    <v-chip v-for="role in item.roles" :key="role.id" color="primary" class="mr-2">
      ${role.name}
    </v-chip>
  </template>

  <!-- Actions cell -->
  <template v-slot:item.actions="{ item }">
    <v-icon small class="mr-2" @click="editItem(item)">ti ti-pencil</v-icon>
    <v-icon small @click="deleteItem(item)">ti ti-trash</v-icon>
  </template>
</v-data-table-server>
```

Headers shape:
```javascript
headers: [
  {title: 'ID', value: 'id'},
  {title: 'Name', value: 'name'},
  {title: 'Roles', value: 'roles', sortable: false},
  {title: 'Actions', value: 'actions', sortable: false}
]
```

Refresh handler (from `@update:options`):
```javascript
refresh(options) {
  if (options) {
    this.options = {...this.options, page: options.page, itemsPerPage: options.itemsPerPage};
  }
  axios.get(`/api/things?page=${this.options.page}&per_page=${this.options.itemsPerPage}`)
    .then(res => {
      this.items = res.data.items;
      this.itemsLength = res.data.total;
      if (res.data.perPage) this.options.itemsPerPage = res.data.perPage;
    })
    .catch(err => this.showSnack('Failed to load'));
}
```

Props used: `:items`, `:items-length`, `:headers`, `:page`, `:items-per-page`, `hover`
Events used: `@update:options`, `@click:row`
Slots used: `v-slot:top`, `v-slot:item.{field}`, `v-slot:item.actions`

---

### v-dialog

Toggle with `v-model`. Always add `v-if="dialogBool"` on inner `v-card` to force re-mount.

```html
<v-dialog v-model="edialog" width="660">
  <v-card v-if="edialog">
    <v-toolbar>
      <v-toolbar-title>Editor</v-toolbar-title>
      <template v-slot:append>
        <v-btn @click="edialog=false" size="small" icon="ti ti-x" variant="text"></v-btn>
      </template>
    </v-toolbar>
    <v-card-text>
      <v-text-field label="Name" v-model="eitem.name"></v-text-field>
      <v-textarea label="Description" v-model="eitem.description" rows="3"></v-textarea>
      <v-select label="Roles" :items="roles" item-title="name" v-model="eitem.roles"
                multiple chips clearable return-object></v-select>
      <v-switch color="primary" label="Active" v-model="eitem.active"></v-switch>
    </v-card-text>
    <v-card-actions>
      <v-spacer></v-spacer>
      <v-btn @click="edialog=false" variant="text">Cancel</v-btn>
      <v-btn color="primary" @click="saveItem" variant="elevated">Save</v-btn>
    </v-card-actions>
  </v-card>
</v-dialog>
```

Props used: `v-model`, `width`

---

### v-snackbar

```html
<v-snackbar size="small" class="d-flex" v-model="snackBar" rounded="pill" elevation="25">
  ${snackMessage}
  <template v-slot:actions>
    <v-btn @click="snackBar=false" icon="ti ti-x" class="ml-auto" size="small" variant="text"></v-btn>
  </template>
</v-snackbar>
```

Helper method (every page):
```javascript
showSnack(message) {
  this.snackMessage = message;
  this.snackBar = true;
}
```

Props used: `v-model`, `size`, `rounded`, `elevation`
Slots used: `v-slot:actions`

---

### v-btn

```html
<!-- icon button -->
<v-btn icon="ti ti-x" size="small" variant="text" @click="..."></v-btn>

<!-- text button with prepend icon -->
<v-btn variant="text" href="/change">
  <template v-slot:prepend><i class="ti ti-key"></i></template>
  Change Password
</v-btn>

<!-- action button -->
<v-btn color="primary" variant="elevated" size="small" @click="saveItem">Save</v-btn>
```

Props used: `icon`, `size`, `variant`, `color`, `href`, `v-bind="props"` (from menu activator)
Slots used: `v-slot:prepend`, `v-slot:append`

---

### v-text-field / v-textarea / v-select

Default variant is `outlined` (set globally). No need to specify unless overriding.

```html
<v-text-field label="Name" v-model="eitem.name"></v-text-field>
<v-text-field type="email" v-model="eitem.email" label="Email" required></v-text-field>
<v-text-field type="password" v-model="eitem.password"
  :rules="[v => !!v && v.length >= 12 || 'Min 12 characters']"></v-text-field>
<v-text-field label="Search" v-model="q" hide-details single-line>
  <template v-slot:prepend-inner><i class="ti ti-search mr-2"></i></template>
</v-text-field>
<v-textarea label="Description" v-model="eitem.description" rows="3"></v-textarea>
<v-select label="Roles" :items="roles" item-title="name" v-model="eitem.roles"
          multiple chips clearable return-object></v-select>
```

v-select props: `:items`, `item-title`, `v-model`, `multiple`, `chips`, `clearable`, `return-object`

---

### v-switch

```html
<v-switch color="primary" label="Active" v-model="eitem.active"></v-switch>
```

---

### v-card

Flat by default (elevation 0, rounded 0 from global defaults).

```html
<v-card class="ma-2 mt-12 w-100 h-100">
  <v-toolbar>
    <v-toolbar-title>Title</v-toolbar-title>
    <v-spacer></v-spacer>
  </v-toolbar>
  <v-card-text>...</v-card-text>
  <v-card-actions>
    <v-spacer></v-spacer>
    <v-btn color="primary" variant="elevated">Save</v-btn>
  </v-card-actions>
</v-card>
```

---

### v-menu (user menu pattern)

```html
<v-menu :close-on-content-click="false" location="bottom end" transition="fade-transition">
  <template v-slot:activator="{ props }">
    <v-btn v-bind="props" icon variant="text" size="small">
      <v-avatar color="primary" size="32">...</v-avatar>
    </v-btn>
  </template>
  <v-card min-width="280">...</v-card>
</v-menu>
```

Props used: `:close-on-content-click`, `location`, `transition`, `offset`
Slots used: `v-slot:activator="{ props }"` — bind `props` to activator via `v-bind="props"`

---

### v-list / v-list-item

```html
<v-list density="compact">
  <v-list-item href="/dashboard" title="..." subtitle="...">
    <template v-slot:prepend><i class="ti ti-layout-dashboard mr-3" style="font-size: 18px;"></i></template>
    <v-list-item-title>My Account</v-list-item-title>
    <v-list-item-subtitle>Subtitle text</v-list-item-subtitle>
  </v-list-item>
</v-list>
```

Props: `density` (`compact`, default), `href`, `title`, `subtitle`, `base-color`
Slots: `v-slot:prepend`, `v-slot:append`

---

### v-tooltip

```html
<v-tooltip :text="wsConnected ? 'Connected' : 'Reconnecting...'" location="bottom">
  <template v-slot:activator="{ props }">
    <div v-bind="props">...</div>
  </template>
</v-tooltip>
```

Props: `:text`, `location`

---

### v-app-bar / v-navigation-drawer / v-main (layout shell)

Defined in `layout.html`. Do not redefine in page templates — use `{% block content %}` only.

```html
<v-app id="app" v-cloak>
  <v-layout class="rounded rounded-md">
    <v-app-bar color="surface" density="comfortable" elevation="1">...</v-app-bar>
    <v-navigation-drawer v-model="drawer" :rail="isNavCollapsed" :rail-width="68" width="260">
      ...
    </v-navigation-drawer>
    <v-main class="d-flex align-center justify-center" style="min-height: 300px;">
      {% block content %}{% endblock %}
    </v-main>
  </v-layout>
</v-app>
```

---

### v-avatar / v-chip / v-badge / v-alert / v-divider / v-otp-input

```html
<v-avatar color="primary" size="32"><span>AB</span></v-avatar>
<v-avatar size="16" color="green"></v-avatar>  <!-- status dot -->

<v-chip color="primary" class="mr-2">${role.name}</v-chip>

<!-- v-badge (the shell's bell) -->
<v-badge :model-value="unreadCount > 0" :content="unreadCount" color="agent" offset-x="4" offset-y="4">
  <i class="ti ti-bell"></i>
</v-badge>
<v-badge dot color="primary" inline></v-badge>

<v-alert>...</v-alert>  <!-- used in auth templates -->
<v-divider></v-divider>
<v-otp-input></v-otp-input>  <!-- used in 2FA templates -->
```

---

## Custom stk Components

`registerStkComponents(app)` (`static/js/components/index.js`) registers shared
components; pages call it before mount. The bell is part of the shell in `layout.html`.

---

## Navigation Entry Shape (`app.navigation`)

Each extension's `init_app` appends its entry; the list is in extension order, which
`sealbook/app.py` keeps in rail order: `{title, icon, to, match?, badge?, role?}`. `match` is the path
that marks the item current; a stand-in for a page that does not exist yet leaves it out.
`badge` names a key of `/api/nav/counts`; `role` is a single string filtered by
`filterNavByRole(navItems, userRoles)`. The first five items form the mobile bottom bar.

---

## Template Block Structure

```html
{% extends "layout.html" %}
{% block css %}{% endblock %}      <!-- optional extra CSS -->
{% block content %}               <!-- Vuetify components here -->
{% endblock %}
{% block js %}                    <!-- Vue app script here -->
{% endblock %}
```

`{% block sidebar %}{% endblock %}` and `{% block layout_classes %} align-center {% endblock %}`
are used in full-width pages (e.g. CMS) to suppress the sidebar.
