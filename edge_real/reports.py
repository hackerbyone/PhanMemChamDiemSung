"""Portable XLSX and printable HTML exports; no Office installation required."""
import base64
import csv
import io
import unicodedata
from collections import defaultdict
from html import escape
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as ET
from storage import shooting_day

STATES={'AUTO':'Tự động - chưa duyệt','NEEDS_REVIEW':'Cần kiểm tra','REVIEWED':'Đã kiểm tra','DEMO':'Thử nghiệm DEMO'}
HEADERS=['Mã loạt','Mã người bắn','Họ tên','Đơn vị','Ngày bắn','Loạt bắn','Bệ bắn','Số phát dự kiến','Số lỗ tính','Tổng điểm','Trạng thái','Phiên bản','Ghi chú']

def round_values(p):
    return [p.get('id',''),p.get('shooter_code',''),p.get('shooter',''),p.get('unit',''),shooting_day(p),p.get('series'),p.get('lane'),p.get('expected_shots'),p.get('total_holes',0),p.get('total_score',0),STATES.get(p.get('status'),'Bản cũ'),p.get('revision',1),p.get('notes','')]

def _column(n):
    s=''
    while n:n,r=divmod(n-1,26);s=chr(65+r)+s
    return s

def _sheet(rows):
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    root=ET.Element('worksheet',xmlns=ns)
    views=ET.SubElement(root,'sheetViews');view=ET.SubElement(views,'sheetView',workbookViewId='0')
    ET.SubElement(view,'pane',ySplit='1',topLeftCell='A2',activePane='bottomLeft',state='frozen')
    cols=ET.SubElement(root,'cols')
    for i,header in enumerate(rows[0],1):
        width=min(42,max(14,len(str(header))+3,max((len(str(r[i-1] or '')) for r in rows[1:101]),default=0)+2))
        ET.SubElement(cols,'col',min=str(i),max=str(i),width=str(width),customWidth='1')
    data=ET.SubElement(root,'sheetData')
    for i,values in enumerate(rows,1):
        row=ET.SubElement(data,'row',r=str(i),ht='26' if i==1 else '21',customHeight='1')
        for j,value in enumerate(values,1):
            cell=ET.SubElement(row,'c',r=f'{_column(j)}{i}',s='1' if i==1 else '0')
            if isinstance(value,(int,float)) and not isinstance(value,bool):ET.SubElement(cell,'v').text=str(value)
            else:
                cell.set('t','inlineStr')
                text=''.join(ch for ch in str(value if value is not None else '') if ord(ch)>=32 or ch in '\t\n\r')
                ET.SubElement(ET.SubElement(cell,'is'),'t',{'xml:space':'preserve'}).text=text[:32767]
    ET.SubElement(root,'autoFilter',ref=f'A1:{_column(len(rows[0]))}{len(rows)}')
    ET.SubElement(root,'pageSetup',orientation='landscape',paperSize='9',fitToWidth='1',fitToHeight='0')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)

def export_xlsx(path,payloads):
    if not payloads:raise ValueError('Không có kết quả để xuất')
    people=defaultdict(lambda:[0,0,0]);units=defaultdict(lambda:[0,0,0]);holes=[]
    for p in payloads:
        kind='DEMO' if p.get('status')=='DEMO' or p.get('demo') else 'Thực tế (gồm chưa duyệt)'
        for key,dest in [((p.get('shooter_code',''),p.get('shooter',''),p.get('unit',''),kind),people),((p.get('unit',''),kind),units)]:
            totals=dest[key];totals[0]+=1;totals[1]+=p.get('total_holes',0);totals[2]+=p.get('total_score',0)
        for i,h in enumerate(p.get('detections',[]),1):
            if h['selected']:holes.append([p['id'],p.get('shooter_code',''),p.get('shooter',''),shooting_day(p),p.get('series'),p.get('lane'),i,*h['template_point'],h['score'],h.get('source','model'),STATES.get(p.get('status'),'Bản cũ')])
    sheets=[('Loạt bắn',[HEADERS]+[round_values(p) for p in payloads]),
            ('Theo người',[['Mã người bắn','Họ tên','Đơn vị','Loại dữ liệu','Số loạt','Số lỗ','Tổng điểm']]+[list(k)+v for k,v in sorted(people.items())]),
            ('Theo đơn vị',[['Đơn vị','Loại dữ liệu','Số loạt','Số lỗ','Tổng điểm']]+[list(k)+v for k,v in sorted(units.items())]),
            ('Chi tiết lỗ',[['Mã loạt','Mã người bắn','Họ tên','Ngày bắn','Loạt','Bệ','STT bản ghi','X (pixel mẫu)','Y (pixel mẫu)','Điểm','Nguồn','Trạng thái']]+holes)]
    for _,rows in sheets:
        if len(rows)>1_048_576:raise ValueError('Quá giới hạn dòng Excel; hãy lọc ít loạt hơn')
    with ZipFile(path,'w',ZIP_DEFLATED) as z:
        overrides=''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+overrides+'</Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i,(name,_) in enumerate(sheets,1))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml','<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF17365D"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i,(_,rows) in enumerate(sheets,1):z.writestr(f'xl/worksheets/sheet{i}.xml',_sheet(rows))
    return Path(path)

def print_report(path,payloads,store):
    if not payloads:raise ValueError('Không có kết quả để in')
    parts=[]
    for p in payloads:
        def e(v):return escape(str(v if v is not None else ''))
        rows=''.join('<tr>'+''.join('<td>'+e(v)+'</td>' for v in [i+1,', '.join(f'{x:.1f}' for x in h['template_point']),h['score'],'Thủ công' if h.get('source')=='manual' else 'Model'])+'</tr>' for i,h in enumerate(p.get('detections',[])) if h['selected'])
        folder=store.root/p['id']/f"rev_{p.get('revision',1)}"
        if not folder.exists():folder=store.root/p['id']
        image=folder/'annotated.png'
        picture='<img alt="Điểm trên bia mẫu" src="data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()+'">' if image.exists() else '<p>Không có ảnh chú thích.</p>'
        parts.append(f'''<article><h1>PHIẾU KẾT QUẢ BẮN</h1><p class="state">{e(STATES.get(p.get('status'),'Bản cũ'))}</p>
<dl><dt>Người bắn</dt><dd>{e(p.get('shooter'))} ({e(p.get('shooter_code'))})</dd><dt>Đơn vị</dt><dd>{e(p.get('unit'))}</dd><dt>Ngày bắn</dt><dd>{e(shooting_day(p))}</dd><dt>Loạt / Bệ bắn</dt><dd>{e(p.get('series'))} / {e(p.get('lane'))}</dd><dt>Số phát dự kiến</dt><dd>{e(p.get('expected_shots'))}</dd></dl>
<p class="total">{e(p.get('total_holes',0))} lỗ được tính · {e(p.get('total_score',0))} điểm</p>
<p>Mã loạt: {e(p['id'])} · Phiên bản: {e(p.get('revision',1))}</p><p>Kiểm tra: {e(p.get('reviewed_at','Chưa xác nhận'))}</p><p>Ghi chú: {e(p.get('notes',''))}</p>
<p>{e(' | '.join(p.get('warnings',[])))}</p>{picture}<table><thead><tr><th>STT bản ghi</th><th>Tâm (pixel mẫu)</th><th>Điểm</th><th>Nguồn</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note">STT là số bản ghi, không phải thứ tự phát bắn. Số lỗ quan sát có thể khác số phát trúng chồng. Phiếu giữ nguyên trạng thái kiểm tra của kết quả.</p><div class="sign"><span>Người bắn<br><small>(Ký, ghi rõ họ tên)</small></span><span>Người kiểm tra<br><small>(Ký, ghi rõ họ tên)</small></span></div></article>''')
    html='''<!doctype html><html lang="vi"><meta charset="utf-8"><title>Phiếu kết quả bắn</title><style>
body{font:14px "Segoe UI",sans-serif;color:#152d43;background:#edf1f5;margin:0}article{max-width:740px;margin:24px auto;background:white;padding:28px}h1{text-align:center;font-size:24px}.state{text-align:center;font-weight:bold}dl{display:grid;grid-template-columns:160px 1fr;gap:7px}dt{font-weight:bold}dd{margin:0}.total{font-size:22px;font-weight:bold}img{display:block;max-width:100%;max-height:300px;margin:12px auto}table{width:100%;border-collapse:collapse}th,td{padding:7px;border:1px solid #bac4ce;text-align:left}thead{display:table-header-group}tr{break-inside:avoid}.note{font-size:12px;color:#526272}.sign{display:flex;justify-content:space-around;text-align:center;margin:28px 0 65px}.toolbar{position:sticky;top:0;background:#17365d;color:white;padding:14px;text-align:center}button{padding:10px 25px;cursor:pointer}@page{size:A4;margin:14mm}@media print{body{background:white}article{max-width:none;margin:0;padding:0;break-after:page}article:last-child{break-after:auto}.toolbar{display:none}.sign{break-inside:avoid}}</style><div class="toolbar"><button onclick="window.print()">In phiếu / Lưu PDF</button> Chọn máy in hoặc Save as PDF trong hộp thoại in.</div>'''+''.join(parts)+'</html>'
    Path(path).write_text(html,encoding='utf8');return Path(path)

def _key(value):
    return ''.join(c for c in unicodedata.normalize('NFD',value.lower().replace('đ','d')) if not unicodedata.combining(c)).strip().replace(' ','_')

def import_roster(path):
    text=Path(path).read_text(encoding='utf-8-sig')
    try:dialect=csv.Sniffer().sniff(text[:4096],delimiters=',;\t')
    except csv.Error:dialect=csv.excel
    reader=csv.DictReader(io.StringIO(text),dialect=dialect)
    aliases={'ma_nguoi_ban':'code','ma':'code','shooter_code':'code','ho_ten':'name','ten':'name','shooter':'name','don_vi':'unit'}
    rows=[]
    if not reader.fieldnames:raise ValueError('Danh sách trống')
    headers=[aliases.get(_key(k),_key(k)) for k in reader.fieldnames]
    if len(headers)!=len(set(headers)) or not {'code','name','unit'}.issubset(headers):raise ValueError('CSV cần 3 cột: code,name,unit (hoặc Mã người bắn,Họ tên,Đơn vị)')
    for row in reader:
        if None in row:raise ValueError('Số cột CSV không khớp dòng tiêu đề')
        if any(v and v.strip() for v in row.values()):rows.append({aliases.get(_key(k),_key(k)):v for k,v in row.items()})
    if not rows:raise ValueError('Danh sách không có người bắn')
    return rows

def roster_template(path):
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['code','name','unit']);writer.writerow(['S01','Nguyễn Văn An','Đơn vị A'])
