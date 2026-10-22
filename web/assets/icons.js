/* 图标：和原版 theme/icons.ts 一一对应——Lucide（lucide-react 1.55.0）的逐字节路径数据，
   加上原版 createLucideIcon 生成的 <svg> 外壳（width/height 24、lucide 类名、aria-hidden）。
   GitHub 标志是 The Library Icon Rule 的例外，照抄原版 GitHubIcon.tsx。 */

const LUCIDE = {
  /* nav */
  conversations: "<path d=\"M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719\"/>",
  gifts: "<path d=\"M12 7v14\"/><path d=\"M20 11v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8\"/><path d=\"M7.5 7a1 1 0 0 1 0-5A4.8 8 0 0 1 12 7a4.8 8 0 0 1 4.5-5 1 1 0 0 1 0 5\"/><rect x=\"3\" y=\"7\" width=\"18\" height=\"4\" rx=\"1\"/>",
  settings: "<path d=\"M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/>",
  /* 列表页首 */
  serviceStatus: "<path d=\"M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2\"/>",
  announcements: "<path d=\"M10.268 21a2 2 0 0 0 3.464 0\"/><path d=\"M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326\"/>",
  search: "<path d=\"m21 21-4.34-4.34\"/><circle cx=\"11\" cy=\"11\" r=\"8\"/>",
  filter: "<path d=\"M2 5h20\"/><path d=\"M6 12h12\"/><path d=\"M9 19h6\"/>",
  pin: "<path d=\"M12 17v5\"/><path d=\"M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z\"/>",
  unpin: "<path d=\"M12 17v5\"/><path d=\"M15 9.34V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H7.89\"/><path d=\"m2 2 20 20\"/><path d=\"M9 9v1.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h11\"/>",
  show: "<path d=\"M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/>",
  hide: "<path d=\"M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49\"/><path d=\"M14.084 14.158a3 3 0 0 1-4.242-4.242\"/><path d=\"M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143\"/><path d=\"m2 2 20 20\"/>",
  /* 对话页 */
  phone: "<path d=\"M13.832 16.568a1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 6.392 6.384\"/>",
  more: "<circle cx=\"12\" cy=\"12\" r=\"1\"/><circle cx=\"19\" cy=\"12\" r=\"1\"/><circle cx=\"5\" cy=\"12\" r=\"1\"/>",
  back: "<path d=\"m15 18-6-6 6-6\"/>",
  forward: "<path d=\"m9 18 6-6-6-6\"/>",
  newConversation: "<path d=\"M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7\"/><path d=\"M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z\"/>",
  pastConversations: "",
  send: "<path d=\"m5 12 7-7 7 7\"/><path d=\"M12 19V5\"/>",
  plus: "<path d=\"M5 12h14\"/><path d=\"M12 5v14\"/>",
  picture: "<rect width=\"18\" height=\"18\" x=\"3\" y=\"3\" rx=\"2\" ry=\"2\"/><circle cx=\"9\" cy=\"9\" r=\"2\"/><path d=\"m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21\"/>",
  remove: "<path d=\"M18 6 6 18\"/><path d=\"m6 6 12 12\"/>",
  ended: "<path d=\"m2 2 20 20\"/><path d=\"M4.93 4.929a10 10 0 0 0-1.938 11.412 2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 0 0 11.302-1.989\"/><path d=\"M8.35 2.69A10 10 0 0 1 21.3 15.65\"/>",
  cooldown: "<path d=\"M5 22h14\"/><path d=\"M5 2h14\"/><path d=\"M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22\"/><path d=\"M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2\"/>",
  outfit: "<path d=\"M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z\"/>",
  collapse: "<path d=\"m6 9 6 6 6-6\"/>",
  /* 好感度 */
  heart: "<path d=\"M2 9.5a5.5 5.5 0 0 1 9.591-3.676.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5l-5.492 5.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5\"/>",
  heartCrack: "<path d=\"M12.409 5.824c-.702.792-1.15 1.496-1.415 2.166l2.153 2.156a.5.5 0 0 1 0 .707l-2.293 2.293a.5.5 0 0 0 0 .707L12 15\"/><path d=\"M13.508 20.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5a5.5 5.5 0 0 1 9.591-3.677.6.6 0 0 0 .818.001A5.5 5.5 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5z\"/>",
  heartOff: "<path d=\"M10.5 4.893a5.5 5.5 0 0 1 1.091.931.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 1.872-1.002 3.356-2.187 4.655\"/><path d=\"m16.967 16.967-3.459 3.346a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5a5.5 5.5 0 0 1 2.747-4.761\"/><path d=\"m2 2 20 20\"/>",
  affinityUp: "<path d=\"m5 12 7-7 7 7\"/><path d=\"M12 19V5\"/>",
  affinityDown: "<path d=\"M12 5v14\"/><path d=\"m19 12-7 7-7-7\"/>",
  /* 设置 */
  memories: "<path d=\"M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20\"/><path d=\"M8.62 9.8A2.25 2.25 0 1 1 12 6.836a2.25 2.25 0 1 1 3.38 2.966l-2.626 2.856a.998.998 0 0 1-1.507 0z\"/>",
  stats: "<path d=\"M3 3v16a2 2 0 0 0 2 2h16\"/><path d=\"M18 17V9\"/><path d=\"M13 17V5\"/><path d=\"M8 17v-3\"/>",
  profile: "<circle cx=\"12\" cy=\"12\" r=\"10\"/><circle cx=\"12\" cy=\"10\" r=\"3\"/><path d=\"M7 20.662V19a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v1.662\"/>",
  reachOuts: "<path d=\"M2.992 16.342a2 2 0 0 1 .094 1.167l-1.065 3.29a1 1 0 0 0 1.236 1.168l3.413-.998a2 2 0 0 1 1.099.092 10 10 0 1 0-4.777-4.719\"/><path d=\"M7.828 13.07A3 3 0 0 1 12 8.764a3 3 0 0 1 5.004 2.224 3 3 0 0 1-.832 2.083l-3.447 3.62a1 1 0 0 1-1.45-.001z\"/>",
  appearance: "<path d=\"M12 22a1 1 0 0 1 0-20 10 9 0 0 1 10 9 5 5 0 0 1-5 5h-2.25a1.75 1.75 0 0 0-1.4 2.8l.3.4a1.75 1.75 0 0 1-1.4 2.8z\"/><circle cx=\"13.5\" cy=\"6.5\" r=\".5\" fill=\"currentColor\"/><circle cx=\"17.5\" cy=\"10.5\" r=\".5\" fill=\"currentColor\"/><circle cx=\"6.5\" cy=\"12.5\" r=\".5\" fill=\"currentColor\"/><circle cx=\"8.5\" cy=\"7.5\" r=\".5\" fill=\"currentColor\"/>",
  ownKeys: "<path d=\"M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z\"/><circle cx=\"16.5\" cy=\"7.5\" r=\".5\" fill=\"currentColor\"/>",
  signIn: "<path d=\"m10 17 5-5-5-5\"/><path d=\"M15 12H3\"/><path d=\"M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4\"/>",
  dataPackage: "<path d=\"M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z\"/><path d=\"M12 22V12\"/><polyline points=\"3.29 7 12 12 20.71 7\"/><path d=\"m7.5 4.27 9 5.15\"/>",
  about: "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M12 16v-4\"/><path d=\"M12 8h.01\"/>",
  externalLink: "<path d=\"M15 3h6v6\"/><path d=\"M10 14 21 3\"/><path d=\"M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6\"/>",
  /* 照片 */
  photo: "<path d=\"M13.997 4a2 2 0 0 1 1.76 1.05l.486.9A2 2 0 0 0 18.003 7H20a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h1.997a2 2 0 0 0 1.759-1.048l.489-.904A2 2 0 0 1 10.004 4z\"/><circle cx=\"12\" cy=\"13\" r=\"3\"/>",
  photosUsedUp: "<path d=\"M14.564 14.558a3 3 0 1 1-4.122-4.121\"/><path d=\"m2 2 20 20\"/><path d=\"M20 20H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h1.997a2 2 0 0 0 .819-.175\"/><path d=\"M9.695 4.024A2 2 0 0 1 10.004 4h3.993a2 2 0 0 1 1.76 1.05l.486.9A2 2 0 0 0 18.003 7H20a2 2 0 0 1 2 2v7.344\"/>",
  photoFailed: "<line x1=\"2\" x2=\"22\" y1=\"2\" y2=\"22\"/><path d=\"M10.41 10.41a2 2 0 1 1-2.83-2.83\"/><line x1=\"13.5\" x2=\"6\" y1=\"13.5\" y2=\"21\"/><line x1=\"18\" x2=\"21\" y1=\"12\" y2=\"15\"/><path d=\"M3.59 3.59A1.99 1.99 0 0 0 3 5v14a2 2 0 0 0 2 2h14c.55 0 1.052-.22 1.41-.59\"/><path d=\"M21 15V5a2 2 0 0 0-2-2H9\"/>",
  /* 礼物 */
  gift: "<path d=\"M12 7v14\"/><path d=\"M20 11v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8\"/><path d=\"M7.5 7a1 1 0 0 1 0-5A4.8 8 0 0 1 12 7a4.8 8 0 0 1 4.5-5 1 1 0 0 1 0 5\"/><rect x=\"3\" y=\"7\" width=\"18\" height=\"4\" rx=\"1\"/>",
  draw: "<path d=\"M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z\"/><path d=\"M20 2v4\"/><path d=\"M22 4h-4\"/><circle cx=\"4\" cy=\"20\" r=\"2\"/>",
  becomes: "<path d=\"M5 12h14\"/><path d=\"m12 5 7 7-7 7\"/>",
  food: "<path d=\"M11 17h.01\"/><path d=\"M11.496 2c.324-.016.558.292.529.615a4 4 0 004.235 4.368.713.713 0 01.758.757 4 4 0 004.366 4.237c.323-.03.63.204.614.527a10 10 0 01-2.915 6.566A1 1 0 114.93 4.918 10 10 0 0111.496 2\"/><path d=\"M12 12h.01\"/><path d=\"M16 16h.01\"/><path d=\"M16 3h.01\"/><path d=\"M21 4h.01\"/><path d=\"M21 8h.01\"/><path d=\"M7 14h.01\"/><path d=\"M9 8h.01\"/>",
  drink: "<path d=\"m6 8 1.75 12.28a2 2 0 0 0 2 1.72h4.54a2 2 0 0 0 2-1.72L18 8\"/><path d=\"M5 8h14\"/><path d=\"M7 15a6.47 6.47 0 0 1 5 0 6.47 6.47 0 0 0 5 0\"/><path d=\"m12 8 1-6h2\"/>",
  flower: "<path d=\"M12 5a3 3 0 1 1 3 3m-3-3a3 3 0 1 0-3 3m3-3v1M9 8a3 3 0 1 0 3 3M9 8h1m5 0a3 3 0 1 1-3 3m3-3h-1m-2 3v-1\"/><circle cx=\"12\" cy=\"8\" r=\"2\"/><path d=\"M12 10v12\"/><path d=\"M12 22c4.2 0 7-1.667 7-5-4.2 0-7 1.667-7 5Z\"/><path d=\"M12 22c-4.2 0-7-1.667-7-5 4.2 0 7 1.667 7 5Z\"/>",
  trinket: "<path d=\"M10.5 3 8 9l4 13 4-13-2.5-6\"/><path d=\"M17 3a2 2 0 0 1 1.6.8l3 4a2 2 0 0 1 .013 2.382l-7.99 10.986a2 2 0 0 1-3.247 0l-7.99-10.986A2 2 0 0 1 2.4 7.8l2.998-3.997A2 2 0 0 1 7 3z\"/><path d=\"M2 9h20\"/>",
  sponsor: "<path d=\"M11 14h2a2 2 0 0 0 0-4h-3c-.6 0-1.1.2-1.4.6L3 16\"/><path d=\"m14.45 13.39 5.05-4.694C20.196 8 21 6.85 21 5.75a2.75 2.75 0 0 0-4.797-1.837.276.276 0 0 1-.406 0A2.75 2.75 0 0 0 11 5.75c0 1.2.802 2.248 1.5 2.946L16 11.95\"/><path d=\"m2 15 6 6\"/><path d=\"m7 20 1.6-1.4c.3-.4.8-.6 1.4-.6h4c1.1 0 2.1-.4 2.8-1.2l4.6-4.4a1 1 0 0 0-2.75-2.91\"/>",
  /* 通用 */
  trash: "<path d=\"M10 11v6\"/><path d=\"M14 11v6\"/><path d=\"M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6\"/><path d=\"M3 6h18\"/><path d=\"M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2\"/>",
  failure: "<circle cx=\"12\" cy=\"12\" r=\"10\"/><line x1=\"12\" x2=\"12\" y1=\"8\" y2=\"12\"/><line x1=\"12\" x2=\"12.01\" y1=\"16\" y2=\"16\"/>",
  success: "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"m16 9-5.5 5.5L8 12\"/>",
  close: "<path d=\"M18 6 6 18\"/><path d=\"m6 6 12 12\"/>",
  mic: "<path d=\"M12 19v3\"/><path d=\"M19 10v2a7 7 0 0 1-14 0v-2\"/><rect x=\"9\" y=\"2\" width=\"6\" height=\"13\" rx=\"3\"/>",
  micOff: "<path d=\"M12 19v3\"/><path d=\"M15 9.34V5a3 3 0 0 0-5.68-1.33\"/><path d=\"M16.95 16.95A7 7 0 0 1 5 12v-2\"/><path d=\"M18.89 13.23A7 7 0 0 0 19 12v-2\"/><path d=\"m2 2 20 20\"/><path d=\"M9 9v3a3 3 0 0 0 5.12 2.12\"/>",
  micSwitch: "<path d=\"m16 3 4 4-4 4\"/><path d=\"M20 7H4\"/><path d=\"m8 21-4-4 4-4\"/><path d=\"M4 17h16\"/>",
  hangUp: "<path d=\"M10.1 13.9a14 14 0 0 0 3.732 2.668 1 1 0 0 0 1.213-.303l.355-.465A2 2 0 0 1 17 15h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2 18 18 0 0 1-12.728-5.272\"/><path d=\"M22 2 2 22\"/><path d=\"M4.76 13.582A18 18 0 0 1 2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-.8 1.6l-.468.351a1 1 0 0 0-.292 1.233 14 14 0 0 0 .244.473\"/>",
  textOnly: "<rect width=\"18\" height=\"14\" x=\"3\" y=\"5\" rx=\"2\" ry=\"2\"/><path d=\"M7 15h4M15 15h2M7 11h2M13 11h4\"/>",
  dashboard: "<rect width=\"7\" height=\"9\" x=\"3\" y=\"3\" rx=\"1\"/><rect width=\"7\" height=\"5\" x=\"14\" y=\"3\" rx=\"1\"/><rect width=\"7\" height=\"9\" x=\"14\" y=\"12\" rx=\"1\"/><rect width=\"7\" height=\"5\" x=\"3\" y=\"16\" rx=\"1\"/>",
  megaphone: "<path d=\"M11 6a13 13 0 0 0 8.4-2.8A1 1 0 0 1 21 4v12a1 1 0 0 1-1.6.8A13 13 0 0 0 11 14H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z\"/><path d=\"M6 14a12 12 0 0 0 2.4 7.2 2 2 0 0 0 3.2-2.4A8 8 0 0 1 10 14\"/><path d=\"M8 6v8\"/>",
  config: "<path d=\"M10 5H3\"/><path d=\"M12 19H3\"/><path d=\"M14 3v4\"/><path d=\"M16 17v4\"/><path d=\"M21 12h-9\"/><path d=\"M21 19h-5\"/><path d=\"M21 5h-7\"/><path d=\"M8 10v4\"/><path d=\"M8 12H3\"/>",
  users: "<path d=\"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2\"/><path d=\"M16 3.128a4 4 0 0 1 0 7.744\"/><path d=\"M22 21v-2a4 4 0 0 0-3-3.87\"/><circle cx=\"9\" cy=\"7\" r=\"4\"/>",
  sortable: "<path d=\"m21 16-4 4-4-4\"/><path d=\"M17 20V4\"/><path d=\"m3 8 4-4 4 4\"/><path d=\"M7 4v16\"/>",
  showMore: "<path d=\"m6 9 6 6 6-6\"/>",
  showLess: "<path d=\"m18 15-6-6-6 6\"/>",
  check: "<path d=\"M20 6 9 17l-5-5\"/>",
  minus: "<path d=\"M5 12h14\"/>",
  share: "<circle cx=\"18\" cy=\"5\" r=\"3\"/><circle cx=\"6\" cy=\"12\" r=\"3\"/><circle cx=\"18\" cy=\"19\" r=\"3\"/><line x1=\"8.59\" x2=\"15.42\" y1=\"13.51\" y2=\"17.49\"/><line x1=\"15.41\" x2=\"8.59\" y1=\"6.51\" y2=\"10.49\"/>",
  spinner: "<path d=\"M21 12a9 9 0 1 1-6.219-8.56\"/>",
  helpCircle: "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M12 16v-4\"/><path d=\"M12 8h.01\"/>",
};

/** 每个图标的 Lucide 原名（<svg> 的 class 用，和原版一致）。 */
const NAMES = {
  conversations: "message-circle",
  gifts: "gift",
  settings: "settings",
  serviceStatus: "activity",
  announcements: "bell",
  search: "search",
  filter: "list-filter",
  pin: "pin",
  unpin: "pin-off",
  show: "eye",
  hide: "eye-off",
  phone: "phone",
  more: "ellipsis",
  back: "chevron-left",
  forward: "chevron-right",
  newConversation: "square-pen",
  pastConversations: "history",
  send: "arrow-up",
  plus: "plus",
  picture: "image",
  remove: "x",
  ended: "message-circle-off",
  cooldown: "hourglass",
  outfit: "shirt",
  collapse: "chevron-down",
  heart: "heart",
  heartCrack: "heart-crack",
  heartOff: "heart-off",
  affinityUp: "arrow-up",
  affinityDown: "arrow-down",
  memories: "book-heart",
  stats: "chart-column",
  profile: "circle-user",
  reachOuts: "message-circle-heart",
  appearance: "palette",
  ownKeys: "key-round",
  signIn: "log-in",
  dataPackage: "package",
  about: "info",
  externalLink: "external-link",
  photo: "camera",
  photosUsedUp: "camera-off",
  photoFailed: "image-off",
  gift: "gift",
  draw: "sparkles",
  becomes: "arrow-right",
  food: "cookie",
  drink: "cup-soda",
  flower: "flower-2",
  trinket: "gem",
  sponsor: "hand-heart",
  trash: "trash",
  failure: "circle-alert",
  success: "circle-check",
  close: "x",
  mic: "mic",
  micOff: "mic-off",
  micSwitch: "arrow-right-left",
  hangUp: "phone-off",
  textOnly: "captions",
  dashboard: "layout-dashboard",
  megaphone: "megaphone",
  config: "sliders-horizontal",
  users: "users",
  sortable: "arrow-up-down",
  showMore: "chevron-down",
  showLess: "chevron-up",
  check: "check",
  minus: "minus",
  share: "share-2",
  spinner: "loader-circle",
  helpCircle: "info",
};

/* 品牌（GitHub，非 Lucide —— The Library Icon Rule 的例外）：照抄原版 GitHubIcon.tsx。 */
const GITHUB = '<path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>';

/** 一个图标：<svg> 外壳和原版 createLucideIcon 生成的一样（24px、stroke 2、round、currentColor）。 */
export function icon(name, cls = "size-5") {
  if (name === "github") {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${GITHUB}</svg>`;
  }
  const body = LUCIDE[name] || "";
  const lucide = `lucide lucide-${NAMES[name] || name}`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="${lucide} ${cls}" aria-hidden="true">${body}</svg>`;
}

/** 实心的图标（心形的好感度用 fill）。 */
export function iconFilled(name, cls = "size-5") {
  const body = LUCIDE[name] || "";
  const lucide = `lucide lucide-${NAMES[name] || name}`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="${lucide} ${cls}" aria-hidden="true">${body}</svg>`;
}

/** 五档的心形（原版 TierHeart）：冷淡（断）、疏远（裂）、信任（空）、亲近（半）、亲密（满）。
 * 亲近是空心叠一层只露下半的实心；页首标识里是她的颜色（text-her-mark）。 */
export function heartIcon(state, cls = "size-4") {
  const look = `${cls} text-her-mark`;
  const heart = (fill, extra = "") =>
    `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="${fill}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-heart ${look}${extra}" aria-hidden="true">${LUCIDE.heart}</svg>`;
  if (state === "cold") return icon("heartOff", look);
  if (state === "distant") return icon("heartCrack", look);
  if (state === "close") {
    return `<span aria-hidden class="relative flex shrink-0">${heart("none")}${heart("currentColor", " absolute inset-0 [clip-path:inset(50%_0_0_0)]")}</span>`;
  }
  if (state === "intimate") return `<span aria-hidden class="relative flex shrink-0">${heart("currentColor")}</span>`;
  return `<span aria-hidden class="relative flex shrink-0">${heart("none")}</span>`;
}