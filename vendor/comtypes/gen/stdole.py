from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    VgaColor, OLE_XSIZE_CONTAINER, StdPicture, COMMETHOD, IFont, GUID,
    Default, FontEvents, _check_version, OLE_XSIZE_HIMETRIC, Checked,
    IPictureDisp, FONTNAME, VARIANT_BOOL, OLE_COLOR, OLE_OPTEXCLUSIVE,
    FONTSIZE, OLE_CANCELBOOL, Gray, CoClass, IPicture, dispid,
    FONTSTRIKETHROUGH, Picture, OLE_XPOS_PIXELS, OLE_YSIZE_HIMETRIC,
    OLE_XPOS_HIMETRIC, IFontEventsDisp, OLE_YPOS_CONTAINER,
    DISPMETHOD, Monochrome, OLE_YSIZE_CONTAINER, IUnknown,
    IEnumVARIANT, StdFont, OLE_YPOS_PIXELS, FONTITALIC,
    OLE_YSIZE_PIXELS, BSTR, Font, EXCEPINFO, OLE_XSIZE_PIXELS,
    Library, DISPPROPERTY, _lcid, typelib_path, HRESULT, IFontDisp,
    IDispatch, DISPPARAMS, Unchecked, OLE_HANDLE, OLE_YPOS_HIMETRIC,
    FONTUNDERSCORE, FONTBOLD, OLE_ENABLEDEFAULTBOOL,
    OLE_XPOS_CONTAINER, Color
)


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


__all__ = [
    'VgaColor', 'StdFont', 'OLE_TRISTATE', 'OLE_XSIZE_CONTAINER',
    'StdPicture', 'OLE_YPOS_PIXELS', 'FONTITALIC', 'IFont',
    'OLE_YSIZE_PIXELS', 'Font', 'Default', 'FontEvents',
    'OLE_XSIZE_HIMETRIC', 'OLE_XSIZE_PIXELS', 'Library', 'Checked',
    'IPictureDisp', 'LoadPictureConstants', 'FONTNAME', 'OLE_COLOR',
    'typelib_path', 'OLE_OPTEXCLUSIVE', 'IFontDisp', 'FONTSIZE',
    'OLE_CANCELBOOL', 'Gray', 'IPicture', 'FONTSTRIKETHROUGH',
    'Unchecked', 'Picture', 'OLE_HANDLE', 'OLE_YPOS_HIMETRIC',
    'FONTUNDERSCORE', 'OLE_XPOS_PIXELS', 'OLE_YSIZE_HIMETRIC',
    'FONTBOLD', 'OLE_XPOS_HIMETRIC', 'IFontEventsDisp',
    'OLE_YPOS_CONTAINER', 'OLE_ENABLEDEFAULTBOOL',
    'OLE_XPOS_CONTAINER', 'Monochrome', 'OLE_YSIZE_CONTAINER', 'Color'
]

