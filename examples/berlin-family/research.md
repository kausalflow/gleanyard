# Research brief

- **Idea:** Every public event in Berlin that families with children can attend, in one dataset
  that is refreshed daily, so a parent can ask "what is on this weekend near me, for a
  four-year-old, for free?".
- **Records look like:** see `schema.yaml`: title, dates and time, venue and address, age
  range, free or priced, indoor or outdoor, category and the page it came from.
- **In scope:**
  - Events inside Berlin (postcode 10001 to 14199) that are meant for children or whole
    families: story hours, workshops, children's theatre and concerts, family tours and days,
    holiday programmes, festivals with a children's programme.
  - Future dates only. Recurring events as separate dated records.
  - German or English listings; keep the title as published.
- **Out of scope:** adult-only events, events that merely allow children, daycare and school
  timetables, regular opening hours without a dated programme, events outside Berlin, ticket
  resale.
- **Preferred source types:** feeds (`.ics`, RSS, JSON) before HTML pages, and HTML with
  schema.org `Event` data before pages that need selectors. Venue pages with an events
  feed beat aggregators; use an aggregator only as a lead, because the venue is the source of
  truth and aggregators often forbid reuse.
- **Where to look first:** the city's own event calendar and district (Bezirk) calendars,
  public libraries, museums and zoos with family programmes, children's and youth theatres,
  community and family centres, and park and nature education programmes. These are
  categories to search, not verified sources: each candidate still goes through `probe`,
  `check` and a reading of its terms before it is proposed.
- **Rights:** read each site's imprint and terms before collecting. Titles, dates, venues,
  ages and prices are facts; descriptions and images are protected and stay out of the export
  unless a source's terms allow them.
