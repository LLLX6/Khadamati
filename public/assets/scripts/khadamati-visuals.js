(function () {
  'use strict';

  // Khadamati subject artwork. Navigation/actions keep familiar UI icons;
  // categories and services use these semantic, two-tone pictograms.
  const glyphs = Object.freeze({
    home: '<path class="kh-pic-fill" d="M4 11.4 12 5l8 6.4V20H4Z"/><path class="kh-pic-line" d="m3 12 9-7 9 7M6 10.5V20h12v-9.5M9 20v-5h6v5"/><path class="kh-pic-accent" d="m16.8 4.2.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8Z"/>',
    clean: '<path class="kh-pic-fill" d="M8 8h8l2 12H6Z"/><path class="kh-pic-line" d="M9 8V5h6v3M7 12h10M8 8h8l2 12H6Z"/><path class="kh-pic-accent" d="m18 3 .7 1.8 1.8.7-1.8.7L18 8l-.7-1.8-1.8-.7 1.8-.7Z"/>',
    transport: '<path class="kh-pic-fill" d="M3 8h11v9H3Zm11 4h4l3 3v2h-7Z"/><path class="kh-pic-line" d="M3 8h11v9H3Zm11 4h4l3 3v2h-7M6 17a2 2 0 1 0 4 0m7 0a2 2 0 1 0 4 0"/><path class="kh-pic-accent" d="M6 10h5v4H6Z"/>',
    construction: '<path class="kh-pic-fill" d="M4 10h9v10H4Zm11-4h5v14h-5Z"/><path class="kh-pic-line" d="M4 10h9v10H4Zm11-4h5v14h-5M7 13h3m-3 3h3m10-7h-5"/><path class="kh-pic-accent" d="M14 3h7v3h-7Z"/>',
    tech: '<rect class="kh-pic-fill" x="3" y="5" width="18" height="12" rx="2"/><path class="kh-pic-line" d="M3 5h18v12H3ZM8 21h8m-4-4v4"/><path class="kh-pic-accent" d="m8 10 2-2 2 2-2 2Zm6-2h3v4h-3Z"/>',
    car: '<path class="kh-pic-fill" d="m5 11 2-5h10l2 5 2 2v5H3v-5Z"/><path class="kh-pic-line" d="m5 11 2-5h10l2 5 2 2v5H3v-5Zm0 7v2m14-2v2M6 13h2m8 0h2"/><path class="kh-pic-accent" d="M9 14h6v2H9Z"/>',
    event: '<path class="kh-pic-fill" d="M7 4c2.2 0 4 2.1 4 4.7S9.2 14 7 14 3 11.3 3 8.7 4.8 4 7 4Zm10 1c2.2 0 4 2.1 4 4.7S19.2 15 17 15s-4-2.7-4-5.3S14.8 5 17 5Z"/><path class="kh-pic-line" d="M7 14v6m10-5v5M5 20h4m6 0h4"/><path class="kh-pic-accent" d="m12 2 .7 1.8 1.8.7-1.8.7L12 8l-.7-1.8-1.8-.7 1.8-.7Z"/>',
    education: '<path class="kh-pic-fill" d="M3 5h7a3 3 0 0 1 3 3v12H6a3 3 0 0 0-3 2Zm18 0h-7a3 3 0 0 0-3 3v12h7a3 3 0 0 1 3 2Z"/><path class="kh-pic-line" d="M3 5h7a3 3 0 0 1 3 3v12H6a3 3 0 0 0-3 2Zm18 0h-7a3 3 0 0 0-3 3v12h7a3 3 0 0 1 3 2Z"/><path class="kh-pic-accent" d="M6 9h4v2H6Zm8 0h4v2h-4Z"/>',
    personal: '<circle class="kh-pic-fill" cx="12" cy="8" r="4"/><path class="kh-pic-line" d="M12 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8ZM4 21c1.2-5.5 14.8-5.5 16 0"/><path class="kh-pic-accent" d="m18 5 .7 1.8 1.8.7-1.8.7L18 11l-.7-1.8-1.8-.7 1.8-.7Z"/>',
    cooling: '<rect class="kh-pic-fill" x="3" y="4" width="18" height="8" rx="2"/><path class="kh-pic-line" d="M3 4h18v8H3Zm4 4h10M8 16c1-2 2-2 3 0s2 2 3 0m-7 4c1-2 2-2 3 0s2 2 3 0"/><path class="kh-pic-accent" d="M17 15v7m-3.1-5.2 6.2 3.4m0-3.4-6.2 3.4"/>',
    pest: '<path class="kh-pic-fill" d="M8 9h8v7a4 4 0 0 1-8 0Z"/><path class="kh-pic-line" d="M9 9V7a3 3 0 0 1 6 0v2M8 9h8v7a4 4 0 0 1-8 0ZM4 12h4m8 0h4M5 7l3 2m11-2-3 2M12 9v11"/><path class="kh-pic-accent" d="M3 3h5v2H3Zm13 16h5v2h-5Z"/>',
    decor: '<path class="kh-pic-fill" d="M5 12h14a2 2 0 0 1 2 2v5H3v-5a2 2 0 0 1 2-2Z"/><path class="kh-pic-line" d="M5 12V9a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v3m-14 0h14a2 2 0 0 1 2 2v5H3v-5a2 2 0 0 1 2-2Zm2 7v2m10-2v2"/><path class="kh-pic-accent" d="M10 8h4v4h-4Z"/>',
    garden: '<path class="kh-pic-fill" d="M12 21V10c-5 0-8-2.5-8-7 5 0 8 2.5 8 7 0-4 3-6 8-6 0 4-3 6-8 6"/><path class="kh-pic-line" d="M12 21V10M4 3c5 0 8 2.5 8 7-5 0-8-2.5-8-7Zm16 1c0 4-3 6-8 6 0-4 3-6 8-6ZM7 21h10"/><path class="kh-pic-accent" d="M5 15h4v4H5Z"/>',
    design: '<path class="kh-pic-fill" d="M4 16 15.5 4.5l4 4L8 20H4Z"/><path class="kh-pic-line" d="M4 16 15.5 4.5l4 4L8 20H4Zm9-9 4 4M4 20h16"/><path class="kh-pic-accent" d="M4 16h4v4H4Z"/>',
    marketing: '<path class="kh-pic-fill" d="m4 10 12-5v14L4 14Z"/><path class="kh-pic-line" d="m4 10 12-5v14L4 14Zm0 0v4m12-5c3 0 4 1 4 3s-1 3-4 3M7 15l1 5h4l-2-4"/><path class="kh-pic-accent" d="M19 4h3v2h-3Zm1 14h3v2h-3Z"/>',
    tailoring: '<path class="kh-pic-fill" d="M5 8h14v11H5Z"/><path class="kh-pic-line" d="M5 8h14v11H5Zm3 0V5h8v3M8 12h4m-4 3h8M15 11v5"/><path class="kh-pic-accent" d="M17 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4Z"/>',
    care: '<path class="kh-pic-fill" d="M12 20S4 15.5 4 9.5C4 5 9 3 12 7c3-4 8-2 8 2.5 0 6-8 10.5-8 10.5Z"/><path class="kh-pic-line" d="M12 20S4 15.5 4 9.5C4 5 9 3 12 7c3-4 8-2 8 2.5 0 6-8 10.5-8 10.5Z"/><path class="kh-pic-accent" d="M9 12h2v-2h2v2h2v2h-2v2h-2v-2H9Z"/>',
    office: '<path class="kh-pic-fill" d="M6 3h8l4 4v14H6Z"/><path class="kh-pic-line" d="M6 3h8l4 4v14H6Zm8 0v5h5M9 12h6m-6 4h6"/><path class="kh-pic-accent" d="M3 15h5v6H3Z"/>',
    corporate: '<path class="kh-pic-fill" d="M4 7h16v13H4Z"/><path class="kh-pic-line" d="M4 7h16v13H4Zm5 0V4h6v3M4 12h16M9 12v2h6v-2"/><path class="kh-pic-accent" d="M7 16h4v2H7Z"/>',
    gift: '<path class="kh-pic-fill" d="M4 9h16v12H4Z"/><path class="kh-pic-line" d="M4 9h16v12H4Zm8 0v12M3 9h18V6H3Zm9-3c-3 0-5-1-5-3 3 0 5 1 5 3Zm0 0c3 0 5-1 5-3-3 0-5 1-5 3Z"/><path class="kh-pic-accent" d="M9 9h6v4H9Z"/>',
    property: '<path class="kh-pic-fill" d="m4 11 8-6 8 6v9H4Z"/><path class="kh-pic-line" d="m3 12 9-7 9 7M5 11v9h14v-9M9 20v-5h6v5"/><path class="kh-pic-accent" d="M16 5a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm3-3h3m-1 0v3"/>',
    tourism: '<path class="kh-pic-fill" d="M5 8h14v12H5Z"/><path class="kh-pic-line" d="M5 8h14v12H5Zm4 0V5h6v3M5 13h14M9 11v4m6-4v4"/><path class="kh-pic-accent" d="M12 14a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"/>',
    emergency: '<path class="kh-pic-fill" d="M12 3 2.5 20h19Z"/><path class="kh-pic-line" d="M12 3 2.5 20h19ZM12 9v5"/><path class="kh-pic-accent" d="M12 16.5a1.2 1.2 0 1 0 0 2.4 1.2 1.2 0 0 0 0-2.4Z"/>',
    electric: '<path class="kh-pic-fill" d="m13 2-8 12h7l-1 8 8-12h-7Z"/><path class="kh-pic-line" d="m13 2-8 12h7l-1 8 8-12h-7Z"/><circle class="kh-pic-accent" cx="18" cy="5" r="2"/>',
    plumbing: '<path class="kh-pic-fill" d="M12 3S5 10 5 15a7 7 0 0 0 14 0c0-5-7-12-7-12Z"/><path class="kh-pic-line" d="M12 3S5 10 5 15a7 7 0 0 0 14 0c0-5-7-12-7-12ZM9 16a3 3 0 0 0 3 3"/><path class="kh-pic-accent" d="M15 9h5v2h-5Z"/>',
    appliance: '<rect class="kh-pic-fill" x="5" y="3" width="14" height="18" rx="2"/><path class="kh-pic-line" d="M5 3h14v18H5Zm0 6h14M9 6h.01M14 6h2"/><circle class="kh-pic-accent" cx="12" cy="15" r="3"/>',
    curtains: '<path class="kh-pic-fill" d="M5 5h14v16H5Z"/><path class="kh-pic-line" d="M3 5h18M6 5c0 5 3 7 6 7S18 10 18 5M6 5v16m12-16v16M12 12v9"/><path class="kh-pic-accent" d="M4 3h16v3H4Z"/>',
    furniture: '<path class="kh-pic-fill" d="M5 10h14a2 2 0 0 1 2 2v7H3v-7a2 2 0 0 1 2-2Z"/><path class="kh-pic-line" d="M5 10V7a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v3m-14 0h14a2 2 0 0 1 2 2v7H3v-7a2 2 0 0 1 2-2Zm1 9v2m16-2v2M12 10v9"/><path class="kh-pic-accent" d="M7 12h3v3H7Z"/>',
    paint: '<path class="kh-pic-fill" d="M4 4h12v6H4Zm7 6h3v4h-3Zm0 4h3v8h-3Z"/><path class="kh-pic-line" d="M4 4h12v6H4Zm12 3h3v4h-5m-3-1h3v4h-3Zm0 4h3v8h-3Z"/><path class="kh-pic-accent" d="M5 5h10v4H5Z"/>',
    tank: '<path class="kh-pic-fill" d="M6 4h12v16H6Z"/><path class="kh-pic-line" d="M6 4h12v16H6ZM6 8h12M6 16h12M9 20v2m6-2v2"/><path class="kh-pic-accent" d="M12 10s-3 3-3 5a3 3 0 0 0 6 0c0-2-3-5-3-5Z"/>',
    lock: '<rect class="kh-pic-fill" x="5" y="10" width="14" height="11" rx="2"/><path class="kh-pic-line" d="M8 10V7a4 4 0 0 1 8 0v3M5 10h14v11H5Z"/><path class="kh-pic-accent" d="M12 13a2 2 0 0 1 1 3.7V19h-2v-2.3a2 2 0 0 1 1-3.7Z"/>',
    pool: '<path class="kh-pic-fill" d="M3 15c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2 2-2 4-2v7H3Z"/><path class="kh-pic-line" d="M3 15c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2 2-2 4-2M4 19c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2M8 13V5h7m-7 4h7V5"/><path class="kh-pic-accent" d="M15 3h3v3h-3Z"/>',
    satellite: '<path class="kh-pic-fill" d="M5 7a10 10 0 0 0 12 12Z"/><path class="kh-pic-line" d="M5 7a10 10 0 0 0 12 12ZM11 13l7-7m-4-1a5 5 0 0 1 5 5m-7-8a10 10 0 0 1 10 10M11 17l-2 4h8"/><circle class="kh-pic-accent" cx="11" cy="13" r="2"/>',
    smart: '<path class="kh-pic-fill" d="m4 11 8-6 8 6v9H4Z"/><path class="kh-pic-line" d="m3 12 9-7 9 7M5 11v9h14v-9M9 15a4 4 0 0 1 6 0m-4 2a1.5 1.5 0 0 1 2 0"/><circle class="kh-pic-accent" cx="12" cy="19" r="1.4"/>',
    heater: '<rect class="kh-pic-fill" x="6" y="3" width="12" height="18" rx="4"/><path class="kh-pic-line" d="M6 7a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4Zm3 2h6"/><path class="kh-pic-accent" d="M12 11s-2.5 2.7-2.5 4.5a2.5 2.5 0 0 0 5 0c0-1.8-2.5-4.5-2.5-4.5Z"/>',
    sofa: '<path class="kh-pic-fill" d="M4 11h16v8H4Z"/><path class="kh-pic-line" d="M6 11V8a3 3 0 0 1 3-3h6a3 3 0 0 1 3 3v3m-14 0a2 2 0 0 0-2 2v6h20v-6a2 2 0 0 0-2-2M6 19v2m12-2v2M12 6v13"/><path class="kh-pic-accent" d="M7 12h4v4H7Z"/>',
    carpet: '<rect class="kh-pic-fill" x="5" y="3" width="14" height="18" rx="2"/><path class="kh-pic-line" d="M5 3h14v18H5Zm3 4h8v10H8Zm-3-2H3m2 4H3m2 4H3m2 4H3m16-12h2m-2 4h2m-2 4h2m-2 4h2"/><path class="kh-pic-accent" d="m12 9 3 3-3 3-3-3Z"/>',
    window: '<rect class="kh-pic-fill" x="4" y="3" width="16" height="18" rx="1"/><path class="kh-pic-line" d="M4 3h16v18H4Zm8 0v18M4 12h16"/><path class="kh-pic-accent" d="m17 5 .6 1.4L19 7l-1.4.6L17 9l-.6-1.4L15 7l1.4-.6Z"/>',
    kitchen: '<path class="kh-pic-fill" d="M4 3h16v18H4Z"/><path class="kh-pic-line" d="M4 3h16v18H4Zm0 8h16M9 3v8m6 0v10M7 6h.01m10 8h.01"/><path class="kh-pic-accent" d="M10 14h3v4h-3Z"/>',
    bathroom: '<path class="kh-pic-fill" d="M3 12h18v5a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"/><path class="kh-pic-line" d="M3 12h18v5a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4ZM6 12V6a3 3 0 0 1 6 0M3 16h18M7 21v2m10-2v2"/><path class="kh-pic-accent" d="M11 5h4v2h-4Z"/>',
    sanitize: '<path class="kh-pic-fill" d="M12 3 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6Z"/><path class="kh-pic-line" d="M12 3 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6Z"/><path class="kh-pic-accent" d="M11 8h2v3h3v2h-3v3h-2v-3H8v-2h3Z"/>',
    moving: '<path class="kh-pic-fill" d="M4 8h11v9H4Zm11 4h4l2 3v2h-6Z"/><path class="kh-pic-line" d="M4 8h11v9H4Zm11 4h4l2 3v2h-6M7 17a2 2 0 1 0 4 0m7 0a2 2 0 1 0 4 0M6 5h7"/><path class="kh-pic-accent" d="M7 10h5v4H7Z"/>',
    parcel: '<path class="kh-pic-fill" d="m4 7 8-4 8 4v10l-8 4-8-4Z"/><path class="kh-pic-line" d="m4 7 8-4 8 4-8 4Zm0 0 8 4 8-4v10l-8 4-8-4Zm8 4v10M8 5l8 4"/><path class="kh-pic-accent" d="M5 14h4v3H5Z"/>',
    route: '<path class="kh-pic-fill" d="M7 3a3 3 0 0 0-3 3c0 3 3 5 3 5s3-2 3-5a3 3 0 0 0-3-3Zm10 10a3 3 0 0 0-3 3c0 3 3 5 3 5s3-2 3-5a3 3 0 0 0-3-3Z"/><path class="kh-pic-line" d="M7 3a3 3 0 0 0-3 3c0 3 3 5 3 5s3-2 3-5a3 3 0 0 0-3-3Zm10 10a3 3 0 0 0-3 3c0 3 3 5 3 5s3-2 3-5a3 3 0 0 0-3-3ZM8 11c1 3 6 1 8 3"/><path class="kh-pic-accent" d="M6 5h2v2H6Zm10 10h2v2h-2Z"/>',
    driver: '<circle class="kh-pic-fill" cx="12" cy="7" r="4"/><path class="kh-pic-line" d="M12 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8ZM5 21c1-5 13-5 14 0M7 16h10"/><path class="kh-pic-accent" d="M8 4h8v3H8Z"/>',
    airport: '<path class="kh-pic-fill" d="m3 13 18-8-7 8 5 5-2 1-6-4-4 4-2-1 3-6Z"/><path class="kh-pic-line" d="m3 13 18-8-7 8 5 5-2 1-6-4-4 4-2-1 3-6Z"/><path class="kh-pic-accent" d="M3 21h18v2H3Z"/>',
    bus: '<rect class="kh-pic-fill" x="4" y="3" width="16" height="17" rx="3"/><path class="kh-pic-line" d="M4 6a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v14H4Zm0 5h16M7 20v2m10-2v2M7 15h2m6 0h2"/><path class="kh-pic-accent" d="M7 6h10v3H7Z"/>',
    brick: '<path class="kh-pic-fill" d="M3 5h18v14H3Z"/><path class="kh-pic-line" d="M3 5h18v14H3ZM3 10h18M3 15h18M8 5v5m8-5v5m-5 0v5m-5 0v4m10-4v4"/><path class="kh-pic-accent" d="M4 6h3v3H4Z"/>',
    tile: '<path class="kh-pic-fill" d="M4 4h16v16H4Z"/><path class="kh-pic-line" d="M4 4h16v16H4Zm8 0v16M4 12h16"/><path class="kh-pic-accent" d="m12 6 4 6-4 6-4-6Z"/>',
    metal: '<path class="kh-pic-fill" d="m5 4 6 6-2 2-6-6Zm14 0-6 6 2 2 6-6ZM9 12h6v9H9Z"/><path class="kh-pic-line" d="m5 4 6 6-2 2-6-6Zm14 0-6 6 2 2 6-6ZM9 12h6v9H9Z"/><path class="kh-pic-accent" d="M10 15h4v3h-4Z"/>',
    carpentry: '<path class="kh-pic-fill" d="M4 15 17 2l5 5L9 20H4Z"/><path class="kh-pic-line" d="M4 15 17 2l5 5L9 20H4Zm11-11 5 5M4 20h8"/><path class="kh-pic-accent" d="M5 15h4v4H5Z"/>',
    insulation: '<path class="kh-pic-fill" d="m3 8 9-5 9 5v12H3Z"/><path class="kh-pic-line" d="m3 8 9-5 9 5v12H3Zm4 0 5-3 5 3v9H7Z"/><path class="kh-pic-accent" d="M10 10h4v5h-4Z"/>',
    engineering: '<path class="kh-pic-fill" d="M5 4h14v16H5Z"/><path class="kh-pic-line" d="M5 4h14v16H5Zm3 4h8m-8 4h5m-5 4h8"/><path class="kh-pic-accent" d="m17 8 4 4-4 4Z"/>',
    demolition: '<path class="kh-pic-fill" d="m4 18 7-7 6 6-4 4H4Z"/><path class="kh-pic-line" d="m4 18 7-7 6 6-4 4H4Zm10-9 3-3 4 4-3 3M3 21h18"/><path class="kh-pic-accent" d="M4 4h3v3H4Zm5-2h2v3H9Z"/>',
    phone: '<rect class="kh-pic-fill" x="7" y="2" width="10" height="20" rx="2"/><path class="kh-pic-line" d="M7 2h10v20H7Zm0 4h10M7 18h10"/><circle class="kh-pic-accent" cx="12" cy="20" r="1"/>',
    camera: '<path class="kh-pic-fill" d="M3 7h5l2-3h4l2 3h5v13H3Z"/><path class="kh-pic-line" d="M3 7h5l2-3h4l2 3h5v13H3Z"/><circle class="kh-pic-accent" cx="12" cy="13" r="4"/>',
    network: '<circle class="kh-pic-fill" cx="12" cy="19" r="2"/><path class="kh-pic-line" d="M3 8a14 14 0 0 1 18 0M6 12a9 9 0 0 1 12 0m-9 4a4 4 0 0 1 6 0"/><circle class="kh-pic-accent" cx="12" cy="19" r="2"/>',
    code: '<rect class="kh-pic-fill" x="3" y="4" width="18" height="16" rx="2"/><path class="kh-pic-line" d="M3 4h18v16H3ZM3 8h18m-12 4-3 2 3 2m6-4 3 2-3 2"/><path class="kh-pic-accent" d="M12 11h2l-2 6h-2Z"/>',
    printer: '<path class="kh-pic-fill" d="M6 9V3h12v6m0 8v4H6v-4Z"/><path class="kh-pic-line" d="M6 9V3h12v6m0 8v4H6v-4Zm-12 0H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2"/><path class="kh-pic-accent" d="M8 14h8v4H8Z"/>',
    data: '<ellipse class="kh-pic-fill" cx="12" cy="5" rx="7" ry="3"/><path class="kh-pic-line" d="M5 5c0 1.7 3.1 3 7 3s7-1.3 7-3-3.1-3-7-3-7 1.3-7 3Zm0 0v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7"/><path class="kh-pic-accent" d="M8 10h2v2H8Z"/>',
    security: '<path class="kh-pic-fill" d="M12 3 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6Z"/><path class="kh-pic-line" d="M12 3 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6Z"/><path class="kh-pic-accent" d="M8 12h8v6H8Zm2 0V9a2 2 0 0 1 4 0v3"/>',
    battery: '<rect class="kh-pic-fill" x="3" y="7" width="17" height="10" rx="2"/><path class="kh-pic-line" d="M3 7h17v10H3Zm17 3h2v4h-2M7 12h4m-2-2v4"/><path class="kh-pic-accent" d="M14 10h3v4h-3Z"/>',
    tire: '<circle class="kh-pic-fill" cx="12" cy="12" r="9"/><path class="kh-pic-line" d="M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18Zm0 5a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm-6-2 3 3m6 6 3 3m0-12-3 3m-6 6-3 3"/><circle class="kh-pic-accent" cx="12" cy="12" r="2"/>',
    inspect: '<path class="kh-pic-fill" d="m5 11 2-5h10l2 5 2 2v5H3v-5Z"/><path class="kh-pic-line" d="m5 11 2-5h10l2 5 2 2v5H3v-5Z"/><path class="kh-pic-accent" d="M17 14a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm2 5 3 3"/>',
    tow: '<path class="kh-pic-fill" d="M3 8h10v9H3Zm10 4h5l3 3v2h-8Z"/><path class="kh-pic-line" d="M3 8h10v9H3Zm10 4h5l3 3v2h-8M6 17a2 2 0 1 0 4 0m8 0a2 2 0 1 0 4 0M5 5h6m-3-3v6"/><path class="kh-pic-accent" d="M7 3h3v3H7Z"/>',
    oil: '<path class="kh-pic-fill" d="M5 8h13v10H5Z"/><path class="kh-pic-line" d="M5 8h13v10H5Zm3-4h6v4M3 10h2m13 1 3 2v4M8 12h6"/><path class="kh-pic-accent" d="M18 5s-2 2-2 3.5a2 2 0 0 0 4 0C20 7 18 5 18 5Z"/>',
    key: '<circle class="kh-pic-fill" cx="8" cy="9" r="5"/><path class="kh-pic-line" d="M8 4a5 5 0 1 1 0 10A5 5 0 0 1 8 4Zm4 9 9 9m-5-5 2-2m1 5 2-2"/><circle class="kh-pic-accent" cx="8" cy="9" r="2"/>',
    photo: '<rect class="kh-pic-fill" x="3" y="6" width="18" height="14" rx="2"/><path class="kh-pic-line" d="M3 6h5l2-3h4l2 3h5v14H3Z"/><circle class="kh-pic-accent" cx="12" cy="13" r="4"/>',
    coffee: '<path class="kh-pic-fill" d="M4 9h13v8a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4Z"/><path class="kh-pic-line" d="M4 9h13v8a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4Zm13 2h2a3 3 0 0 1 0 6h-2M8 3c-2 2 2 2 0 4m5-4c-2 2 2 2 0 4"/><path class="kh-pic-accent" d="M7 12h7v3H7Z"/>',
    music: '<path class="kh-pic-fill" d="M10 5v13a3 3 0 1 1-2-3V7l11-3v11a3 3 0 1 1-2-3V5Z"/><path class="kh-pic-line" d="M10 5v13a3 3 0 1 1-2-3V7l11-3v11a3 3 0 1 1-2-3V5Z"/><path class="kh-pic-accent" d="M10 8h9v3h-9Z"/>',
    flowers: '<circle class="kh-pic-fill" cx="12" cy="10" r="3"/><path class="kh-pic-line" d="M12 7c-1-5-6-3-4 1-5-1-5 4-1 4-3 4 1 7 4 3 1 5 6 3 4-1 5 1 5-4 1-4 3-4-1-7-4-3Zm0 6v9m0-4c-3 0-5-1-6-3m6 4c3 0 5-1 6-3"/><circle class="kh-pic-accent" cx="12" cy="10" r="2"/>',
    catering: '<path class="kh-pic-fill" d="M4 15h16a8 8 0 0 0-16 0Z"/><path class="kh-pic-line" d="M4 15h16a8 8 0 0 0-16 0ZM2 18h20M12 5V3"/><path class="kh-pic-accent" d="M8 11h8v3H8Z"/>',
    kids: '<circle class="kh-pic-fill" cx="12" cy="9" r="5"/><path class="kh-pic-line" d="M7 8c0-3 2-5 5-5s5 2 5 5v3a5 5 0 0 1-10 0Zm-3 13c1-5 15-5 16 0M9 10h.01m6 0h.01m-5 3c1 1 3 1 4 0"/><path class="kh-pic-accent" d="M6 4h4v3H6Z"/>',
    makeup: '<path class="kh-pic-fill" d="M8 10h8v11H8Z"/><path class="kh-pic-line" d="M8 10h8v11H8Zm2 0V5h4v5M9 15h6"/><path class="kh-pic-accent" d="m12 3 2 2h-4Z"/>',
    language: '<path class="kh-pic-fill" d="M4 4h16v13H9l-5 4Z"/><path class="kh-pic-line" d="M4 4h16v13H9l-5 4ZM8 8h8m-6 4h4"/><path class="kh-pic-accent" d="M7 7h3v3H7Z"/>',
    math: '<rect class="kh-pic-fill" x="3" y="3" width="18" height="18" rx="3"/><path class="kh-pic-line" d="M3 3h18v18H3ZM8 7v6m-3-3h6m4-3h4m-4 5h4m-4 4h4M5 17l5-5m-5 0 5 5"/><path class="kh-pic-accent" d="M14 6h6v7h-6Z"/>',
    writing: '<path class="kh-pic-fill" d="M4 16 15.5 4.5l4 4L8 20H4Z"/><path class="kh-pic-line" d="M4 16 15.5 4.5l4 4L8 20H4Zm9-9 4 4M3 22h18"/><path class="kh-pic-accent" d="M4 16h4v4H4Z"/>',
    tutor: '<circle class="kh-pic-fill" cx="9" cy="8" r="4"/><path class="kh-pic-line" d="M9 4a4 4 0 1 1 0 8A4 4 0 0 1 9 4ZM2 21c1-5 13-5 14 0m2-12v8m-3-5h6"/><path class="kh-pic-accent" d="M16 4h6v4h-6Z"/>',
    quran: '<path class="kh-pic-fill" d="M4 5h7a3 3 0 0 1 3 3v12H7a3 3 0 0 0-3 2Zm16 0h-3a3 3 0 0 0-3 3v12h3a3 3 0 0 1 3 2Z"/><path class="kh-pic-line" d="M4 5h7a3 3 0 0 1 3 3v12H7a3 3 0 0 0-3 2Zm16 0h-3a3 3 0 0 0-3 3v12h3a3 3 0 0 1 3 2Z"/><path class="kh-pic-accent" d="m9 9 .7 1.5 1.6.2-1.1 1.1.3 1.6-1.5-.8-1.5.8.3-1.6-1.1-1.1 1.6-.2Z"/>',
    science: '<path class="kh-pic-fill" d="M9 3h6v4l5 11a2 2 0 0 1-2 3H6a2 2 0 0 1-2-3L9 7Z"/><path class="kh-pic-line" d="M9 3h6M10 3v4L5 18a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3L14 7V3M7 15h10"/><circle class="kh-pic-accent" cx="10" cy="17" r="1.5"/>',
    university: '<path class="kh-pic-fill" d="m3 9 9-5 9 5-9 5Z"/><path class="kh-pic-line" d="m3 9 9-5 9 5-9 5Zm4 2v6c3 3 7 3 10 0v-6m4-2v7"/><path class="kh-pic-accent" d="M9 13h6v4H9Z"/>',
    barber: '<circle class="kh-pic-fill" cx="7" cy="7" r="3"/><path class="kh-pic-line" d="M4 7a3 3 0 1 1 6 0 3 3 0 0 1-6 0Zm0 10a3 3 0 1 1 6 0 3 3 0 0 1-6 0Zm5-8 11 8M9 15 20 7"/><path class="kh-pic-accent" d="M14 10h5v3h-5Z"/>',
    laundry: '<rect class="kh-pic-fill" x="4" y="3" width="16" height="18" rx="2"/><path class="kh-pic-line" d="M4 3h16v18H4Zm0 5h16M8 5h.01m3 0h.01"/><circle class="kh-pic-accent" cx="12" cy="14" r="4"/>',
    perfume: '<path class="kh-pic-fill" d="M7 9h10v12H7Z"/><path class="kh-pic-line" d="M7 9h10v12H7Zm3 0V5h4v4M9 3h6"/><path class="kh-pic-accent" d="M10 12h4v5h-4Z"/>',
    beauty: '<circle class="kh-pic-fill" cx="12" cy="12" r="8"/><path class="kh-pic-line" d="M12 4a8 8 0 1 1 0 16 8 8 0 0 1 0-16ZM8 11h.01m8 0h.01m-7 4c2 2 4 2 6 0"/><path class="kh-pic-accent" d="m18 3 .6 1.4L20 5l-1.4.6L18 7l-.6-1.4L16 5l1.4-.6Z"/>',
    elder: '<circle class="kh-pic-fill" cx="10" cy="7" r="4"/><path class="kh-pic-line" d="M10 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8ZM3 21c1-5 13-5 14 0m2-9v10m-2-8h4"/><path class="kh-pic-accent" d="M7 6h6v2H7Z"/>',
    pet: '<path class="kh-pic-fill" d="M7 11c-4 2-3 8 2 8 2 0 2-2 3-2s1 2 3 2c5 0 6-6 2-8-2-1-3-4-5-4s-3 3-5 4Z"/><path class="kh-pic-line" d="M7 11c-4 2-3 8 2 8 2 0 2-2 3-2s1 2 3 2c5 0 6-6 2-8-2-1-3-4-5-4s-3 3-5 4Z"/><circle class="kh-pic-accent" cx="6" cy="6" r="2"/><circle class="kh-pic-accent" cx="18" cy="6" r="2"/>',
    document: '<path class="kh-pic-fill" d="M6 3h8l4 4v14H6Z"/><path class="kh-pic-line" d="M6 3h8l4 4v14H6Zm8 0v5h5M9 12h6m-6 4h6"/><path class="kh-pic-accent" d="M8 6h3v3H8Z"/>'
  });

  const categoryMap = Object.freeze({
    homecare: 'home', cleaning: 'clean', transport: 'transport', moving: 'transport', construction: 'construction',
    tech: 'tech', cars: 'car', events: 'event', education: 'education', personal: 'personal', cooling: 'cooling',
    pestcontrol: 'pest', decor: 'decor', gardens: 'garden', design_dev: 'design', marketing_ads: 'marketing',
    tailoring: 'tailoring', home_assistance: 'care', office_services: 'office', corporate: 'corporate',
    printing_gifts: 'gift', realestate: 'property', tourism: 'tourism', emergency: 'emergency'
  });

  const serviceMap = Object.freeze({
    electrician:'electric',plumber:'plumbing',ac:'cooling',appliances:'appliance',curtains:'curtains',furniture:'furniture',paint:'paint',gypsum:'decor',pest:'pest',tanks:'tank',doors:'lock',gardens:'garden',pools:'pool',satellite:'satellite',smart_home:'smart',water_heater:'heater',
    home_clean:'clean',apt_clean:'home',majlis:'sofa',sofa:'sofa',carpet:'carpet',post_build:'construction',office_clean:'office',facade:'window',deep_clean:'clean',kitchen_clean:'kitchen',bath_clean:'bathroom',mattress:'sofa',sterilize:'sanitize',maid_hourly:'care',
    furniture_move:'moving',items_delivery:'parcel',within_wilayah:'route',between_gov:'route',loading:'parcel',private_driver:'driver',small_truck:'transport',large_truck:'moving',cold_delivery:'cooling',airport:'airport',school_bus:'bus',heavy_equipment:'construction',parcel:'document',
    building:'brick',renovation:'construction',tiles:'tile',marble:'tile',aluminium:'window',metal:'metal',carpentry:'carpentry',insulation:'insulation',roof:'home',glass:'window',plaster:'paint',blocks:'brick',survey:'route',engineering:'engineering',demolition:'demolition',
    pc:'tech',phone_repair:'phone',cameras:'camera',networks:'network',websites:'code',design:'design',tech_support:'tech',pos:'corporate',printer:'printer',data_recovery:'data',apps:'phone',marketing:'marketing',cyber:'security',apple:'tech',
    car_electric:'electric',mechanic:'car',car_wash:'clean',battery:'battery',tires:'tire',inspection:'inspect',tow:'tow',polish:'clean',oil:'oil',ac_car:'cooling',keys:'key',tint:'window',paintless:'metal',diagnostics:'tech',detailing:'clean',
    photo:'photo',party:'event',hospitality:'care',coffee:'coffee',wedding:'flowers',dj:'music',flowers:'flowers',equip:'event',video:'camera',sound:'music',catering:'catering',kids_party:'kids',chairs:'furniture',makeup:'makeup',
    english:'language',math:'math',arabic:'writing',private_tutor:'tutor',quran:'quran',computer_train:'tech',vocational:'construction',physics:'science',chemistry:'science',ielts:'document',kids_learning:'kids',university:'university',
    barber:'barber',men_care:'beauty',tailor:'tailoring',ironing:'tailoring',laundry:'laundry',perfume:'perfume',home_help:'care',beauty:'beauty',massage:'care',elder_care:'elder',pet_care:'pet',documents:'document',
    ac_repair:'cooling',ac_clean:'clean',ac_install:'cooling',ac_gas:'cooling',fridge_repair:'appliance',freezer_repair:'appliance',cockroach:'pest',ants:'pest',bedbugs:'pest',rodents:'pest',home_sanitize:'sanitize',
    interior_design:'decor',wallpaper:'paint',lighting:'electric',parquet:'tile',kitchens:'kitchen',majlis_decor:'sofa',garden_design:'garden',tree_cut:'garden',grass_install:'garden',farm_care:'garden',irrigation:'plumbing',
    logo_design:'design',ad_design:'design',website_design:'code',website_dev:'code',social_manage:'marketing',video_edit:'camera',product_photo:'photo',social_ads:'marketing',campaigns:'marketing',content_design:'writing',commercial_photo:'photo',project_marketing:'marketing',
    women_tailor:'tailoring',men_tailor:'tailoring',clothes_alter:'tailoring',abaya:'tailoring',kumma_mussar:'tailoring',elder_care_plus:'elder',babysitter:'kids',hourly_help:'care',home_organize:'clean',
    printing_docs:'printer',copy_docs:'document',letters:'writing',data_entry:'data',presentations:'design',file_format:'document',office_maintenance:'construction',office_cleaning:'clean',attendance:'tech',business_pos:'corporate',brand_identity:'design',admin_services:'office',
    mug_print:'printer',shirt_print:'printer',sticker_print:'printer',promo_gifts:'gift',signboards:'marketing',property_photo:'photo',apartment_manage:'property',rental_clean:'clean',property_maintenance:'home',property_broker:'property',
    trip_plan:'tourism',booking_help:'document',tour_guide:'route',airport_receive:'airport',locksmith:'lock',water_leak:'plumbing',emergency_ac:'cooling'
  });

  const pickerKeys = Object.freeze([
    'home','clean','transport','construction','tech','car','event','education','personal','cooling','pest','decor','garden','design','marketing','tailoring','care','office','corporate','gift','property','tourism','emergency','electric','plumbing','appliance','curtains','furniture','paint','tank','lock','pool','satellite','smart','heater','sofa','carpet','window','kitchen','bathroom','sanitize','moving','parcel','route','driver','airport','bus','brick','tile','metal','carpentry','insulation','engineering','demolition','phone','camera','network','code','printer','data','security','battery','tire','inspect','tow','oil','key','photo','coffee','music','flowers','catering','kids','makeup','language','math','writing','tutor','quran','science','university','barber','laundry','perfume','beauty','elder','pet','document'
  ]);

  function toneFor(value) {
    const text = String(value || 'khadamati');
    let hash = 0;
    for (let i = 0; i < text.length; i += 1) hash = ((hash << 5) - hash + text.charCodeAt(i)) | 0;
    return Math.abs(hash) % 6;
  }

  function categoryKey(id, storedKey) {
    return glyphs[storedKey] ? storedKey : (categoryMap[id] || 'home');
  }

  function serviceKey(id, categoryId, storedKey) {
    return glyphs[storedKey] ? storedKey : (serviceMap[id] || categoryMap[categoryId] || 'home');
  }

  function render(key, identity, extraClass) {
    const selected = glyphs[key] ? key : 'home';
    const css = ['kh-subject-art', `kh-tone-${toneFor(identity || selected)}`, extraClass || ''].filter(Boolean).join(' ');
    return `<svg class="${css}" viewBox="0 0 24 24" data-pictogram="${selected}" aria-hidden="true" focusable="false">${glyphs[selected]}</svg>`;
  }

  window.KhadamatiVisuals = Object.freeze({ glyphs, categoryMap, serviceMap, pickerKeys, categoryKey, serviceKey, render, toneFor });
}());
