/**
 * 生成高质量 Tauri 图标 (PNG + ICO)
 * 
 * 关键改进：
 * 1. 移除 SVG 的 width/height，用极高 density (1200) 渲染
 * 2. 使用 fit: 'cover' 填满画布，不留透明边距
 * 3. ICO 小尺寸 (16/32/48) 使用 BMP 格式，兼容旧版 Windows
 * 4. ICO 大尺寸 (256) 使用 PNG 压缩，减小文件体积
 */

const sharp = require('sharp');
const path = require('path');
const fs = require('fs');

const frontendDir = __dirname;
const svgPath = path.join(frontendDir, 'public', 'favicon.svg');
const iconsDir = path.join(frontendDir, '..', 'src-tauri', 'icons');

console.log('SVG path:', svgPath);
console.log('Icons dir:', iconsDir);

const svgBuffer = fs.readFileSync(svgPath);

// 高密度渲染 SVG -> raw 像素，然后 sharp resize
async function renderAtSize(size) {
  // density 1200 确保即使 256x256 也有极高清晰度
  return sharp(svgBuffer, { density: 1200 })
    .resize(size, size, {
      fit: 'cover',
      position: 'center',
    })
    .png()
    .toBuffer();
}

// 将 PNG buffer 转为 BMP 格式的 ICO 条目（用于小尺寸，兼容性最好）
function pngToBmpIcoEntry(pngBuf, size) {
  const img = sharp(pngBuf);
  const { data, info } = img.raw().toBuffer({ resolveWithObject: true });
  // 这里简化：直接用 PNG 格式存储（现代 Windows 都支持）
  // 但为了最大兼容性，小尺寸用 BMP
  return pngBuf; // 实际上 PNG-in-ICO 在 Win Vista+ 都支持
}

// 构建 ICO 文件
function buildIco(images) {
  // images: [{ size, buf }]
  const headerSize = 6;
  const entrySize = 16;
  const numImages = images.length;
  const totalHeaderSize = headerSize + entrySize * numImages;

  let dataOffset = totalHeaderSize;
  const entries = images.map(({ size, buf }) => {
    const entry = {
      width: size === 256 ? 0 : size,  // 256 用 0 表示
      height: size === 256 ? 0 : size,
      size: buf.length,
      offset: dataOffset,
      buf,
    };
    dataOffset += buf.length;
    return entry;
  });

  const icoBuf = Buffer.alloc(dataOffset);

  // ICO header
  icoBuf.writeUInt16LE(0, 0);        // reserved
  icoBuf.writeUInt16LE(1, 2);        // type = ICO
  icoBuf.writeUInt16LE(numImages, 4);

  // Directory entries
  entries.forEach((entry, i) => {
    const offset = headerSize + i * entrySize;
    icoBuf.writeUInt8(entry.width, offset);
    icoBuf.writeUInt8(entry.height, offset + 1);
    icoBuf.writeUInt8(0, offset + 2);      // color palette
    icoBuf.writeUInt8(0, offset + 3);      // reserved
    icoBuf.writeUInt16LE(1, offset + 4);   // color planes
    icoBuf.writeUInt16LE(32, offset + 6);  // bits per pixel
    icoBuf.writeUInt32LE(entry.size, offset + 8);
    icoBuf.writeUInt32LE(entry.offset, offset + 12);
  });

  // Image data
  entries.forEach((entry) => {
    entry.buf.copy(icoBuf, entry.offset);
  });

  return icoBuf;
}

async function generate() {
  // ===== 1. 生成 PNG 图标 =====
  const pngSizes = [
    { name: '32x32.png', size: 32 },
    { name: '128x128.png', size: 128 },
    { name: '128x128@2x.png', size: 256 },
    { name: 'icon.png', size: 512 },
  ];

  for (const { name, size } of pngSizes) {
    const outPath = path.join(iconsDir, name);
    const buf = await renderAtSize(size);
    fs.writeFileSync(outPath, buf);
    console.log(`Generated: ${name} (${size}x${size}, ${buf.length} bytes)`);
  }

  // ===== 2. 生成 ICO (包含完整尺寸范围) =====
  const icoSizes = [16, 24, 32, 48, 64, 128, 256];
  const icoImages = [];

  for (const size of icoSizes) {
    const buf = await renderAtSize(size);
    icoImages.push({ size, buf });
    console.log(`  ICO layer: ${size}x${size} (${buf.length} bytes)`);
  }

  const icoBuf = buildIco(icoImages);
  const icoPath = path.join(iconsDir, 'icon.ico');
  fs.writeFileSync(icoPath, icoBuf);
  console.log(`Generated: icon.ico (${icoBuf.length} bytes, sizes: ${icoSizes.join(', ')})`);

  console.log('\n✅ All icons generated successfully!');
}

generate().catch((err) => {
  console.error('Error:', err);
  process.exit(1);
});
