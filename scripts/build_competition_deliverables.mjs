/**
 * Editable competition defence deck. Run with the bundled Node.js runtime.
 * No experiment is executed here. All factual content comes from content.json.
 *
 * Input: { title, subtitle?, status, report_status?, manifest_path?, sources?,
 *   slides:[{title,kind?,paragraphs?,table?:{headers,rows,column_widths?},
 *            formula?:string|string[],notes?:string|string[],status?,footnote?}] }
 *
 * Typical command:
 *   node scripts/build_competition_deliverables.mjs --content outputs/competition_20261001/C5/content.json
 * Optional: --name defence_pending_manifest_v1
 * Outputs remain under outputs/competition_20261001/C5/deck_build/.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import crypto from 'node:crypto';

const workspaceDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const buildDir = path.join(workspaceDir, 'outputs', 'competition_20261001', 'C5', 'deck_build');
const runtimeRoot = process.env.CODEX_DEPENDENCY_ROOT ?? 'C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies';
const runtimeModules = path.join(runtimeRoot, 'node/node_modules');
const RUNTIME_PYTHON = path.join(runtimeRoot, 'python/python.exe');
const SKILL_DIR = process.env.PRESENTATIONS_SKILL_DIR ?? 'C:/Users/Lenovo/.codex/plugins/cache/openai-primary-runtime/presentations/26.915.20218/skills/presentations';
process.env.RUNTIME_NODE_MODULES = runtimeModules;
process.env.RUNTIME_NODE = process.execPath;
process.env.RUNTIME_PYTHON = RUNTIME_PYTHON;
const args = Object.fromEntries(process.argv.slice(2).reduce((out, value, i, all) => {
  if (value.startsWith('--')) out.push([value.slice(2), all[i + 1]?.startsWith('--') ? true : all[i + 1] ?? true]);
  return out;
}, []));
const inputPath = path.resolve(workspaceDir, args.content ?? 'outputs/competition_20261001/C5/content.json');
await fs.mkdir(buildDir, { recursive: true });
const moduleLink = path.join(buildDir, 'node_modules');
try { await fs.symlink(runtimeModules, moduleLink, 'junction'); }
catch (error) { if (error.code !== 'EEXIST') throw error; }
const runtimeRequire = createRequire(path.join(buildDir, 'package.json'));
const artifactPath = runtimeRequire.resolve('@oai/artifact-tool');
const { Presentation, PresentationFile, FileBlob } = await import(pathToFileURL(artifactPath).href);
const artifactRequire = createRequire(artifactPath);
const { Canvas, FontLibrary } = await import(pathToFileURL(artifactRequire.resolve('skia-canvas')).href);
const { finalizePresentation, resolvePresentationFont, applyPresentationChartFont } = await import(pathToFileURL(path.join(SKILL_DIR, 'container_tools/artifact_tool_utils.mjs')).href);
const fontFiles = ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/msyhbd.ttc'];
for (const file of fontFiles) await fs.access(file);
FontLibrary.use('Microsoft YaHei', fontFiles);
const family = resolvePresentationFont({ fontFamily: 'Microsoft YaHei', availableFonts: ['Microsoft YaHei'] });
const measureCtx = new Canvas(1, 1).getContext('2d');
const inputBytes = await fs.readFile(inputPath);
const content = JSON.parse(inputBytes.toString('utf8').replace(/^\uFEFF/, ''));
if (!Array.isArray(content.slides) || !content.slides.length) throw new Error('content.slides must contain at least one slide');
if (!content.title || !content.status) throw new Error('content.title and content.status are required');
const pending = content.status === 'pending_manifest' || !content.manifest_path;
let manifestPath = null;
let manifestSha256 = null;
if (content.manifest_path) {
  manifestPath = path.resolve(workspaceDir, content.manifest_path);
  const data = await fs.readFile(manifestPath);
  manifestSha256 = crypto.createHash('sha256').update(data).digest('hex');
}
if (content.status === 'verified' && pending) throw new Error('verified status requires a real manifest_path');
const stateText = content.report_status ?? (pending ? '待真实 manifest，完整运行结论尚未验收' : '已绑定运行证据，验收状态按各项结果列示');
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const name = String(args.name ?? `competition_defence_${pending ? 'pending_manifest' : 'evidence_bound'}_${stamp}`).replace(/[^\p{L}\p{N}_.-]/gu, '_');
const privateDir = path.join(buildDir, 'private', name);
const deliverDir = path.join(buildDir, 'deliverables');
await fs.mkdir(privateDir, { recursive: true });
await fs.mkdir(deliverDir, { recursive: true });
const finalPath = path.join(deliverDir, `${name}.pptx`);
const draftPath = path.join(privateDir, `${name}.draft.pptx`);
const WHITE = '#FFFFFF', INK = '#142735', MUTED = '#526776', ACCENT = '#135B6E', LINE = '#D6E0E5';
const W = 1280, H = 720, L = 72, RIGHT = 1208, CW = RIGHT - L;
const BODY = 28, FOOT = 20, TITLE = 42;
const presentation = Presentation.create({ slideSize: { width: W, height: H } });
const layoutWarnings = [];
const tableOwners = [];
const chartOwners = [];

function textWidth(text, fontSize, bold = false) {
  measureCtx.font = `${bold ? 'bold ' : ''}${fontSize}px "${family}"`;
  return measureCtx.measureText(String(text)).width;
}

// Explicit line breaks keep PowerPoint's editable text wrapping predictable.
function wrapText(value, width, fontSize, bold = false) {
  const lines = [];
  for (const sourceLine of String(value ?? '').split('\n')) {
    if (!sourceLine) { lines.push(''); continue; }
    let line = '';
    const tokens = sourceLine.match(/[A-Za-z0-9_./:+\-]+|\s+|[^\x00-\x7F]|./gu) ?? [];
    for (let token of tokens) {
      if (!line) token = token.trimStart();
      if (line && textWidth(line + token, fontSize, bold) > width) { lines.push(line.trimEnd()); line = ''; token = token.trimStart(); }
      if (textWidth(token, fontSize, bold) > width) {
        for (const char of token) {
          if (line && textWidth(line + char, fontSize, bold) > width) { lines.push(line); line = ''; }
          line += char;
        }
      } else line += token;
    }
    if (line || !lines.length) lines.push(line.trimEnd());
  }
  return lines.join('\n');
}

function textbox(slide, name, text, x, y, width, height, size = BODY, color = INK, bold = false, align = 'left') {
  const shape = slide.shapes.add({ geometry: 'textbox', name,
    position: { left: x, top: y, width, height }, fill: 'none', line: { fill: 'none', width: 0 } });
  shape.text = text;
  shape.text.style = { typeface: family, fontSize: size, bold, color, alignment: align,
    verticalAlignment: 'top', autoFit: 'none', wrap: 'none', insets: { top: 0, right: 0, bottom: 0, left: 0 } };
  return shape;
}

function block(slide, name, text, y, { size = BODY, color = INK, bold = false, width = CW, x = L, leading = 1.38 } = {}) {
  const wrapped = wrapText(text, width - 6, size, bold);
  const height = wrapped.split('\n').length * size * leading + 5;
  textbox(slide, name, wrapped, x, y, width, height, size, color, bold);
  return height;
}

function safeTextList(value) { return Array.isArray(value) ? value.map(String) : value ? [String(value)] : []; }

function addTable(slide, definition, y, slideNumber) {
  const headers = definition.headers.map(String);
  const dataRows = definition.rows.map(row => row.map(v => String(v ?? '')));
  if (!headers.length || dataRows.some(row => row.length !== headers.length)) throw new Error(`Slide ${slideNumber}: inconsistent table row widths`);
  if (headers.length > 5) throw new Error(`Slide ${slideNumber}: table exceeds five columns, split the evidence across slides`);
  const values = [headers, ...dataRows];
  const weights = definition.column_widths ?? headers.map((_, c) => Math.max(3.8, ...values.map(row => Math.min(16, textWidth(row[c], BODY) / BODY))));
  if (weights.length !== headers.length || weights.some(v => !(v > 0))) throw new Error(`Slide ${slideNumber}: invalid column_widths`);
  const weightSum = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map(v => CW * v / weightSum);
  const wrappedValues = values.map((row, r) => row.map((cell, c) => wrapText(cell, widths[c] - 28, BODY, r === 0)));
  const heights = wrappedValues.map(row => Math.max(...row.map(v => v.split('\n').length)) * BODY * 1.34 + 23);
  const height = heights.reduce((a, b) => a + b, 0);
  if (y + height > 624) layoutWarnings.push({ slide: slideNumber, kind: 'table_dense', bottom: y + height, message: 'Evidence table exceeds recommended content height; shorten cells or split slide.' });
  const table = slide.tables.add({ rows: values.length, columns: headers.length,
    left: L, top: y, width: CW, height, columnWidths: widths, values: wrappedValues });
  table.styleOptions = { headerRow: false, bandedRows: false };
  table.borders.assign({ style: 'solid', fill: LINE, width: 0.7 });
  for (let r = 0; r < values.length; r++) {
    table.rows[r].height = heights[r];
    for (let c = 0; c < headers.length; c++) {
      const cell = table.getCell(r, c);
      cell.fill = r === 0 ? '#EDF3F5' : WHITE;
      cell.text.style = { typeface: family, fontSize: BODY, color: r === 0 ? ACCENT : INK, bold: r === 0,
        verticalAlignment: 'middle', alignment: 'left', autoFit: 'none' };
    }
  }
  table.cells.block({ row: 0, column: 0, rowCount: values.length, columnCount: headers.length }).assign({
    margins: { top: 7, right: 12, bottom: 7, left: 12 }, anchor: 'center', horizontalOverflow: 'clip' });
  tableOwners.push(slideNumber);
  return height;
}

for (let i = 0; i < content.slides.length; i++) {
  const data = content.slides[i];
  const number = i + 1;
  const slide = presentation.slides.add();
  slide.background.fill = WHITE;
  const isCover = data.kind === 'cover';
  let y;
  if (isCover) {
    const h = block(slide, 'cover-title', data.title ?? content.title, 156, { size: 56, bold: true, width: 1100, leading: 1.28 });
    y = 156 + h + 28;
    if (content.subtitle) y += block(slide, 'cover-subtitle', content.subtitle, y, { size: 30, color: ACCENT, width: 1080 }) + 22;
  } else {
    const h = block(slide, 'slide-title', data.title, 50, { size: TITLE, bold: true, leading: 1.18 });
    y = Math.max(156, 50 + h + 22);
  }
  for (let j = 0; j < (data.paragraphs ?? []).length; j++) {
    y += block(slide, `paragraph-${j + 1}`, data.paragraphs[j], y, { size: BODY }) + 19;
  }
  const formulas = safeTextList(data.formula);
  if (formulas.length) {
    if ((data.paragraphs ?? []).length) y += 6;
    for (let j = 0; j < formulas.length; j++) y += block(slide, `formula-${j + 1}`, formulas[j], y, { size: 30, color: ACCENT }) + 16;
  }
  if (data.table) y += addTable(slide, data.table, y + 3, number);
  if (data.chart) {
    const d = data.chart;
    if (!['line', 'bar', 'scatter'].includes(d.type) || !Array.isArray(d.series) || !d.series.length) throw new Error(`Slide ${number}: invalid native evidence chart`);
    for (const series of d.series) {
      if (!series.values?.length || series.values.some(v => !Number.isFinite(v))) throw new Error(`Slide ${number}: missing/nonfinite chart values`);
      if (d.type === 'scatter' && (!series.xValues || series.xValues.length !== series.values.length || series.xValues.some(v => !Number.isFinite(v)))) throw new Error(`Slide ${number}: scatter coordinate mismatch`);
      if (d.type !== 'scatter' && series.values.length !== d.categories.length) throw new Error(`Slide ${number}: chart category/value mismatch`);
    }
    const chart = slide.charts.add(d.type, {
      position: { left: L, top: y, width: CW, height: 420 }, categories: d.categories,
      series: d.series, hasLegend: true, legend: { position: 'bottom', overlay: false },
      ...(d.type === 'bar' ? {barOptions:{direction:'column',grouping:'clustered'}} : {}),
      ...(d.type === 'scatter' ? {scatterOptions:{style:'marker'}} : {}),
      xAxis: { title: d.xTitle ?? (d.type === 'scatter' ? '总服务排放 tCO2' : '时刻/方案'), textStyle:{fontSize:18}, majorGridlines:null },
      yAxis: { title: d.unit, textStyle:{fontSize:18}, numberFormatCode:'0.##', majorGridlines:{fill:LINE,width:0.7} },
    });
    applyPresentationChartFont(chart, {fontFamily:family});
    chartOwners.push(number);
    y += 420;
  }
  const footnote = [data.status, data.footnote].filter(Boolean).join('。');
  if (footnote) {
    const wrapped = wrapText(footnote, CW, FOOT);
    const height = wrapped.split('\n').length * FOOT * 1.28 + 4;
    const top = Math.max(y + 20, 611 - height);
    if (top + height > 638) layoutWarnings.push({ slide: number, kind: 'footnote_dense', bottom: top + height });
    textbox(slide, 'evidence-footnote', wrapped, L, top, CW, height, FOOT, MUTED);
  }
  if (y > 610) layoutWarnings.push({ slide: number, kind: 'body_dense', bottom: y });
  const footer = wrapText(stateText, CW - 110, 18);
  textbox(slide, 'report-state', footer, L, 666, CW - 110, 40, 18, MUTED);
  textbox(slide, 'page-number', `${String(number).padStart(2, '0')} / ${content.slides.length}`, RIGHT - 95, 666, 95, 32, 18, MUTED, false, 'right');
  const notes = safeTextList(data.notes);
  if (data.footnote) notes.push(`可见依据/边界：${data.footnote}`);
  if (manifestPath) notes.push(`运行证据 manifest：${manifestPath}\nSHA-256：${manifestSha256}`);
  if (content.sources?.length && data.source_ids?.length) {
    for (const id of data.source_ids) {
      const source = content.sources.find(item => item.id === id);
      if (!source) throw new Error(`Slide ${number}: unknown source id ${id}`);
      notes.push(`[${source.id}] ${source.title}${source.url ? '\n' + source.url : ''}`);
    }
  }
  slide.speakerNotes.textFrame.setText(notes.join('\n\n'));
}

await fs.writeFile(path.join(privateDir, 'layout-authoring.json'), JSON.stringify({ font: family, fontFiles, layoutWarnings }, null, 2));
if (layoutWarnings.length) throw new Error(`Content exceeds the 28px layout budget. See ${path.join(privateDir, 'layout-authoring.json')}`);
await (await PresentationFile.exportPptx(presentation)).save(draftPath);
const result = await finalizePresentation({
  workspaceDir, candidatePath: draftPath, finalPath, pythonExecutable: RUNTIME_PYTHON,
  integrityValidatorPath: path.join(SKILL_DIR, 'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath: path.join(SKILL_DIR, 'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs: ['--expected-slide-size-emu', '12192000,6858000', '--validate-bullet-geometry', '--validate-heading-fit',
    ...tableOwners.flatMap(number => ['--require-native-table-slide', String(number)])],
  explicitTotalSlideCount: content.slides.length,
  requiredNativeTableOwnerSlides: tableOwners,
  requiredNativeChartOwnerSlides: chartOwners,
  materializeLiteralChartWorkbooks: chartOwners.length > 0,
  fontPolicy: { basis: 'design', families: [family] },
  verifyArtifactToolImport: true,
  receiptPath: path.join(privateDir, `${name}.validation.json`),
});
// Render the finalized package, rather than assuming the authoring object is identical.
const verifiedDeck = await PresentationFile.importPptx(await FileBlob.load(finalPath));
const previews = [];
for (let i = 0; i < verifiedDeck.slides.items.length; i++) {
  const slide = verifiedDeck.slides.items[i];
  const base = `slide-${String(i + 1).padStart(2, '0')}`;
  const pngPath = path.join(privateDir, `${base}.png`);
  const png = await verifiedDeck.export({ slide, format: 'png', scale: 1 });
  await fs.writeFile(pngPath, new Uint8Array(await png.arrayBuffer()));
  const layout = await slide.export({ format: 'layout' });
  await fs.writeFile(path.join(privateDir, `${base}.layout.json`), await layout.text());
  previews.push(pngPath);
}
const montage = await verifiedDeck.export({ format: 'webp', montage: true, scale: 0.4 });
await fs.writeFile(path.join(privateDir, 'montage.webp'), new Uint8Array(await montage.arrayBuffer()));
const buildInfo = { inputPath, inputSha256: crypto.createHash('sha256').update(inputBytes).digest('hex'),
  status: content.status, report_status: stateText, pending_manifest: pending, manifestPath, manifestSha256,
  title: content.title, slideCount: content.slides.length, finalPath, privateDir, nativeTableSlides: tableOwners,
  previews, validation: result, generatedAt: new Date().toISOString(), visualInspection: 'pending' };
await fs.writeFile(path.join(privateDir, 'build-info.json'), JSON.stringify(buildInfo, null, 2));
console.log(JSON.stringify({ finalPath, privateDir, slideCount: content.slides.length, pending_manifest: pending, visualInspection: 'pending' }));
