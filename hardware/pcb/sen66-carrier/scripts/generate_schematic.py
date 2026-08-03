#!/usr/bin/env python3
"""Generate the OMK SEN66 CARRIER Rev.A schematic using kicad-sch-api."""
from __future__ import annotations
import os, re, shutil, subprocess
from pathlib import Path
import kicad_sch_api as ksa
from kicad_sch_api.library.cache import SymbolLibraryCache, set_symbol_cache

ROOT=Path(__file__).resolve().parents[1]; BLANK=ROOT/'sen66-carrier.blank.kicad_sch'
GENERATED=ROOT/'sen66-carrier.generated.kicad_sch'; FORMAL=ROOT/'sen66-carrier.kicad_sch'
ERC=ROOT/'erc-report.txt'; PDF=ROOT/'sen66-carrier-schematic.pdf'; SYMBOLS=Path(os.environ['KICAD_SYMBOL_DIR'])
CACHE=ROOT/'.cache'/'kicad-sch-api'
PINS={'Connector_Generic:Conn_01x06':{'1','2','3','4','5','6'},'Connector_Generic:Conn_01x04':{'1','2','3','4'},'Device:C':{'1','2'},'Device:R':{'1','2'},'Connector:TestPoint':{'1'},'power:PWR_FLAG':{'1'},'power:GND':{'1'}}
SEN66={'1':'+3V3','2':'GND','3':'I2C_SDA','4':'I2C_SCL','5':'GND','6':'+3V3'}
MAIN_J3={'1':'+3V3','2':'GND','3':'I2C_SDA','4':'I2C_SCL'}

def prepare_cache():
 c=SymbolLibraryCache(cache_dir=CACHE,enable_persistence=True); c.discover_libraries([SYMBOLS]); set_symbol_cache(c)
def validate_symbols_and_pins():
 prepare_cache()
 for lib,pins in PINS.items():
  info=ksa.get_symbol_info(lib)
  if info is None or {p['number'] for p in info.list_pins()}!=pins: raise RuntimeError(f'Pin validation failed: {lib}')
def load_or_create_schematic():
 s=ksa.load_schematic(BLANK)
 if len(s.components) or len(s.wires) or len(s.labels): raise RuntimeError('blank template is not empty')
 return s
def add(s,lib,ref,value,pos,fp,mpn,**props):
 fields={'Manufacturer Part Number':mpn,**props}; c=s.components.add(lib,reference=ref,value=value,position=pos,rotation=0,footprint=fp,**fields)
 c.hidden_properties.update({'Footprint','Datasheet',*fields.keys()}); return c
def labels(s,net,pins):
 for ref,pin in pins:s.add_label(net,pin=(ref,pin))
def add_title_block(s): s.set_paper_size('A4'); s.set_title_block(title='OMK SEN66 CARRIER',rev='Rev.A',company='OMK')
def add_sen66_cable_input(s):
 add(s,'Connector_Generic:Conn_01x06','J1','SEN66 CABLE PADS',(45,90),'Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical','Hand solder cable pads',Status='Hand solder; one wire per plated through-hole; DNP BOM')
 for pin,net in SEN66.items(): labels(s,net,[('J1',pin)])
def add_power_merge_block(s):
 add(s,'power:PWR_FLAG','#FLG01','PWR_FLAG',(85,55),'','Internal power source')
 add(s,'power:GND','#PWR01','GND',(85,120),'','Internal ground source')
 add(s,'power:PWR_FLAG','#FLG02','PWR_FLAG',(75,120),'','Internal ground source')
 labels(s,'+3V3',[('#FLG01','1')]); labels(s,'GND',[('#PWR01','1'),('#FLG02','1')])
 s.add_wire_between_pins('#PWR01','1','#FLG02','1')
def add_decoupling(s):
 add(s,'Device:C','C1','100nF',(115,70),'Capacitor_SMD:C_0402_1005Metric','Murata GRM155R71E104KE14D',LCSC='C1525',JLCPCB='Basic candidate')
 add(s,'Device:C','C2','10uF',(130,70),'Capacitor_SMD:C_0805_2012Metric','Murata GRM21BR61A106KE19L',LCSC='C15850',JLCPCB='Basic/Extended availability to verify')
 labels(s,'+3V3',[('C1','1'),('C2','1')]); labels(s,'GND',[('C1','2'),('C2','2')])
def add_optional_i2c_components(s):
 for ref,net,x in [('R1','I2C_SDA',145),('R2','I2C_SCL',155)]:
  add(s,'Device:R',ref,'4.7k DNP',(x,120),'Resistor_SMD:R_0402_1005Metric','Yageo RC0402FR-074K7L',LCSC='C25900',Status='DNP optional pull-up')
  labels(s,'+3V3',[(ref,'1')]); labels(s,net,[(ref,'2')])
def add_main_board_connector(s):
 add(s,'Connector_Generic:Conn_01x04','J2','JST PH 4P',(205,90),'Connector_JST:JST_PH_S4B-PH-K_1x04_P2.00mm_Horizontal','S4B-PH-K-S(LF)(SN)',Manufacturer='JST',LCSC='C157926',Assembly='TH / hand solder or wave solder candidate',Status='Selected; manufacturing release blocked',Notes='Side-entry, keyed; main J3 matching')
 for pin,net in MAIN_J3.items(): labels(s,net,[('J2',pin)])
def add_testpoints(s):
 for ref,net,value,pos in [('TP1','+3V3','3V3',(115,145)),('TP2','GND','GND',(130,145)),('TP3','I2C_SDA','SDA',(145,145)),('TP4','I2C_SCL','SCL',(160,145))]:
  add(s,'Connector:TestPoint',ref,value,pos,'TestPoint:TestPoint_Pad_D1.0mm','PCB test pad',Status='PCB test pad')
  labels(s,net,[(ref,'1')])
def add_power_flags(s): pass
def add_notes(s):
 for text,pos in [('SEN66 JST-GH cable: cut and solder directly',(35,165)),('One wire per plated through-hole; verify pin order',(35,172)),('PCB Phase: provide strain relief and cable retention',(35,179)),('R1/R2: optional I2C pull-ups, DNP',(175,45))]: s.add_text(text,position=pos,size=1.27)
def validate_net_mapping():
 if SEN66 != {'1':'+3V3','2':'GND','3':'I2C_SDA','4':'I2C_SCL','5':'GND','6':'+3V3'} or MAIN_J3 != {'1':'+3V3','2':'GND','3':'I2C_SDA','4':'I2C_SCL'}: raise RuntimeError('Net mapping changed')
def save_and_validate(s):
 s.save_as(GENERATED); subprocess.run(['kicad-cli','sch','erc',str(GENERATED),'--output',str(ERC),'--severity-all'],check=True)
 m=re.search(r'Errors\s+([0-9]+)',ERC.read_text());
 if not m or int(m.group(1)): raise RuntimeError('ERC errors; formal schematic not updated')
 subprocess.run(['kicad-cli','sch','export','pdf',str(GENERATED),'--output',str(PDF)],check=True); shutil.copyfile(GENERATED,FORMAL)
def main():
 validate_symbols_and_pins(); validate_net_mapping(); s=load_or_create_schematic(); add_title_block(s); add_sen66_cable_input(s); add_power_merge_block(s); add_decoupling(s); add_optional_i2c_components(s); add_main_board_connector(s); add_testpoints(s); add_power_flags(s); add_notes(s); save_and_validate(s)
if __name__=='__main__': main()
