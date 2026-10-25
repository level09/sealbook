/**
 * stk Framework - Central Configuration
 * Paper and ink palette with quiet semantic roles
 */

const config = {
    // Common Vue settings
    delimiters: ['${', '}'],

    // Vuetify configuration
    vuetifyConfig: {
        // Tabler icon set for Vuetify's internal component icons (data-table
        // sort/pagination, select dropdown, checkboxes, alerts). Without this
        // Vuetify defaults to mdi aliases, which render blank since we ship no
        // MDI font. Pass-through any explicit "ti ti-*" string; prefix bare names.
        icons: {
            defaultSet: 'tabler',
            sets: {
                tabler: {
                    component: (props) => {
                        const icon = props.icon || '';
                        const cls = (icon.startsWith('ti ') || icon.startsWith('ti-'))
                            ? icon
                            : `ti ti-${icon}`;
                        return Vue.h(props.tag, { class: cls });
                    }
                }
            },
            aliases: {
                complete: 'check',
                cancel: 'circle-x',
                close: 'x',
                delete: 'x',
                clear: 'circle-x',
                success: 'circle-check',
                info: 'info-circle',
                warning: 'alert-triangle',
                error: 'alert-circle',
                prev: 'chevron-left',
                next: 'chevron-right',
                checkboxOn: 'square-check',
                checkboxOff: 'square',
                checkboxIndeterminate: 'square-minus',
                delimiter: 'circle',
                sortAsc: 'arrow-up',
                sortDesc: 'arrow-down',
                expand: 'chevron-down',
                menu: 'menu-2',
                subgroup: 'chevron-down',
                dropdown: 'chevron-down',
                radioOn: 'circle-check',
                radioOff: 'circle',
                edit: 'pencil',
                ratingEmpty: 'star',
                ratingFull: 'star-filled',
                ratingHalf: 'star-half-filled',
                loading: 'loader-2',
                first: 'chevrons-left',
                last: 'chevrons-right',
                unfold: 'arrows-sort',
                file: 'paperclip',
                plus: 'plus',
                minus: 'minus',
                calendar: 'calendar',
                treeviewCollapse: 'chevron-down',
                treeviewExpand: 'chevron-right',
                eyeDropper: 'color-picker',
                upload: 'upload',
                color: 'palette',
                // v-hotkey renders a blank key without these
                command: 'command',
                ctrl: 'chevron-up',
                shift: 'arrow-big-up',
                alt: 'alt',
                enter: 'corner-down-left',
                arrowup: 'arrow-up',
                arrowdown: 'arrow-down',
                arrowleft: 'arrow-left',
                arrowright: 'arrow-right',
                backspace: 'backspace',
                space: 'space'
            }
        },
        defaults: {
            VTextField: {
                variant: 'outlined'
            },
            VSelect: {
                variant: 'outlined'
            },
            VTextarea: {
                variant: 'outlined'
            },
            VCombobox: {
                variant: 'outlined'
            },
            VChip: {
                size: 'small',
                rounded: 'sm'
            },
            VCard: {
                elevation: 0,
                rounded: 'sm'
            },
            VMenu: {
                offset: 10
            },
            VBtn: {
                variant: 'flat',
                rounded: 'sm'
            },
            VDialog: {
                rounded: 'sm'
            },
            VToolbar: {
                elevation: 0
            },
            VDataTableServer: {
                itemsPerPage: 25,
                itemsPerPageOptions: [25, 50, 100]
            }
        },
        theme: {
            defaultTheme: window.__settings__?.dark ? 'dark' : 'light',
            themes: {
                light: {
                    dark: false,
                    colors: {
                        primary: '#1f2124',
                        'on-primary': '#ffffff',
                        secondary: '#555555',
                        accent: '#333333',
                        agent: '#6f5bb0',
                        // Colour roles: mirror of shell.css, see docs/design.md
                        accepted: '#00775c', corroborated: '#00775c', conflict: '#724800', negative: '#ac4a41',
                        sensitive: '#9c5076', due: '#724800', overdue: '#ac4a41', new: '#006cac',
                        person: '#007289', place: '#45751c', source: '#404e60', incident: '#b2471c', story: '#b2471c',
                        error: '#ac4a41',
                        info: '#1f2124',
                        success: '#00775c',
                        warning: '#724800',
                        background: '#ecebe6',
                        surface: '#fbfaf7',
                        'surface-light': '#f5f4f0',
                        'on-surface': '#1f2124',
                    }
                },
                dark: {
                    dark: true,
                    colors: {
                        primary: '#d9dad6',
                        'on-primary': '#1c1d21',
                        secondary: '#a3a3a3',
                        accent: '#d4d4d4',
                        agent: '#b3a1fd',
                        accepted: '#6cd5b3', corroborated: '#6cd5b3', conflict: '#eaa950', negative: '#f2897c',
                        sensitive: '#e694bb', due: '#eaa950', overdue: '#f2897c', new: '#3ba6f5',
                        person: '#50c2df', place: '#d3ef8b', source: '#ccd5e2', incident: '#f0834e', story: '#f0834e',
                        error: '#f2897c',
                        info: '#d9dad6',
                        success: '#6cd5b3',
                        warning: '#eaa950',
                        background: '#151619',
                        surface: '#1c1d21',
                        'surface-light': '#222428',
                        'on-surface': '#d9dad6',
                    }
                }
            }
        }
    }
};
