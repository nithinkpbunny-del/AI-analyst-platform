// English strings for the industry templates (M17). They load with the screens that show a template (the picker on
// Today, the preview in Settings), not with the app shell (DECISIONS D99). The templates' own text (KPIs, starter
// questions) comes from the server in English; translating it later is a data change there.
const templates = {
  templates: {
    previewTitle: 'What this template sets up',
    watches: 'What it keeps an eye on',
    starters: 'Questions to start with',
    words: 'Words it uses: {{words}}',
    loading: 'Loading templates…',
    loadFailed: 'The templates could not be loaded. Reload the page to try again.',
    picker: {
      title: 'What kind of business is it?',
      intro:
        'This shapes the numbers the assistant watches, the questions it suggests and the starting assumptions for what-if scenarios. You can change it later in Settings.',
      choose: 'Kind of business',
      save: 'Use this template',
      saving: 'Saving…',
      saved: 'Template chosen. The suggestions now fit your business.',
    },
  },
}

export default templates
