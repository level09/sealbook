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
                        agent: '#5a46c8',
                        // Colour roles: mirror of shell.css, see docs/DESIGN.md
                        accepted: '#0d6a58', corroborated: '#0d6a58', conflict: '#915c08', negative: '#a04034',
                        sensitive: '#964267', due: '#915c08', overdue: '#a04034', new: '#0d6abb',
                        person: '#4a4c50', place: '#4a4c50', source: '#4a4c50', incident: '#4a4c50', story: '#4a4c50',
                        error: '#a04034',
                        info: '#1f2124',
                        success: '#0d6a58',
                        warning: '#915c08',
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
                        agent: '#ab9ef7',
                        accepted: '#7bd1ba', corroborated: '#7bd1ba', conflict: '#f6b669', negative: '#ea998b',
                        sensitive: '#e9a3bd', due: '#f6b669', overdue: '#ea998b', new: '#77b5fb',
                        person: '#aeb1b5', place: '#aeb1b5', source: '#aeb1b5', incident: '#aeb1b5', story: '#aeb1b5',
                        error: '#ea998b',
                        info: '#d9dad6',
                        success: '#7bd1ba',
                        warning: '#f6b669',
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
