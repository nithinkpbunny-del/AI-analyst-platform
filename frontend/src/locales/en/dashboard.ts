// English strings for the Dashboard screen and the Pin dialog (M15). They load with that code, not with the app shell
// (DECISIONS D99); keep every visible string here so translation stays a data change.
const dashboard = {
  dashboard: {
    loading: 'Loading your dashboard…',
    loadFailed: 'The dashboard could not be loaded. Reload the page to try again.',
    emptyViewer:
      'Nothing is on the dashboard yet. Owners and Analysts can pin answers and show them here.',
    refreshAll: 'Refresh all',
    refreshing: 'Refreshing…',
    yours: 'Your tiles',
    team: 'From your team',
    count: '{{count}} of {{limit}} tiles',
    reorderHint: 'Drag a tile to move it, or use Move up and Move down in its menu.',
    moved: '{{title}} is now number {{position}}.',
    tile: {
      label: 'Tile: {{title}}',
      updated: 'Updated {{when}}',
      computing: 'Getting the latest numbers…',
      notYet: 'Not run for you yet.',
      sharedBy: 'From {{name}}',
      onTeam: 'On everyone’s dashboard',
      vs: 'the period before',
      openAnswer: 'Open the answer',
      moreRows_one: '{{count}} more row',
      moreRows_other: '{{count}} more rows',
      tableLabel: 'Figures for {{title}}',
      errors: {
        no_access: 'You cannot see the data this tile uses.',
        no_sources: 'None of the data you may see can be used right now.',
        no_dictionary: 'This needs a business dictionary. Confirm it first.',
        out_of_date:
          'This tile no longer matches your business dictionary. Ask the question again and pin the new answer.',
        source_error: 'A data source could not be read. Check Connections, then refresh.',
        no_result: 'This tile has nothing to show any more.',
        internal: 'Something went wrong. Try Refresh.',
      },
      notes: {
        fixed_sql_dates: 'Some figures keep the dates of the original question.',
        partly_out_of_date: 'Part of this tile could not be run again.',
      },
    },
    menu: {
      open: 'Options for {{title}}',
      moveUp: 'Move up',
      moveDown: 'Move down',
      settings: 'Tile settings',
      remove: 'Remove from dashboard',
    },
    remove: {
      title: 'Remove this tile?',
      body: 'The answer it came from stays in your history. Nothing in your data changes.',
      confirm: 'Remove tile',
      done: 'Tile removed.',
    },
    settings: {
      title: 'Tile settings',
      name: 'Title',
      refresh: 'Refresh every',
      refreshChoices: {
        '15': '15 minutes',
        '60': 'Hour',
        '240': '4 hours',
        '1440': 'Day',
      },
      share: 'Show on everyone’s dashboard',
      shareHint:
        'Each person sees it with their own access to the data. People who cannot see its data do not see it.',
      save: 'Save tile',
      saved: 'Tile saved.',
      nameError: 'A tile needs a title.',
    },
    dates: {
      label: 'As time passes',
      rolling: 'Move the dates forward',
      rollingHint: '“Last month” stays last month; “last 7 days” stays the last 7 days.',
      fixed: 'Keep these dates',
      fixedHint: 'Always the same period as the answer.',
    },
    pin: {
      title: 'Pin to dashboard',
      intro:
        'The tile runs this answer’s figures again from your live data whenever it refreshes. It does not ask the AI again.',
      name: 'Title',
      submit: 'Pin to dashboard',
      pinning: 'Pinning…',
      done: 'Pinned to your dashboard.',
      open: 'Open dashboard',
    },
  },
}

export default dashboard
