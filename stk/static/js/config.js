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
                        primary: '#18191b',
                        'on-primary': '#ffffff',
                        secondary: '#555555',
                        accent: '#333333',
                        agent: '#5a46c8',
                        // Colour roles: mirror of shell.css, see docs/DESIGN.md
                        accepted: '#0d6a58', corroborated: '#3a6283', conflict: '#915c08', negative: '#a04034',
                        sensitive: '#964267', due: '#915c08', overdue: '#b32e2a', new: '#0d6abb',
                        person: '#0e6a9b', place: '#397247', source: '#81632f', incident: '#9c522e', story: '#485b91',
                        error: '#a04034',
                        info: '#18191b',
                        success: '#0d6a58',
                        warning: '#915c08',
                        background: '#f3f3f1',
                        surface: '#ffffff',
                        'surface-light': '#fafaf8',
                        'on-surface': '#18191b',
                    }
                },
                dark: {
                    dark: true,
                    colors: {
                        primary: '#e7e7e4',
                        'on-primary': '#18191b',
                        secondary: '#a3a3a3',
                        accent: '#d4d4d4',
                        agent: '#ab9ef7',
                        accepted: '#7bd1ba', corroborated: '#98c0e2', conflict: '#f6b669', negative: '#ea998b',
                        sensitive: '#e9a3bd', due: '#f6b669', overdue: '#f6857a', new: '#77b5fb',
                        person: '#84c3ef', place: '#8ed09c', source: '#e0bf89', incident: '#eda382', story: '#a4b6e5',
                        error: '#ea998b',
                        info: '#e7e7e4',
                        success: '#7bd1ba',
                        warning: '#f6b669',
                        background: '#0c0d0f',
                        surface: '#141518',
                        'surface-light': '#1a1c20',
                        'on-surface': '#e7e7e4',
                    }
                }
            }
        }
    }
};
