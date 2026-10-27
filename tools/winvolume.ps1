# List render endpoints, or get/set the volume of the one whose name contains -Match.
#   winvolume.ps1                      list every output device with its volume
#   winvolume.ps1 -Match Audigy -Set 0.03
param([string]$Match = "", [double]$Set = -1)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct PropKey { public Guid fmtid; public uint pid; }
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAudioEndpointVolume {
  int RegisterControlChangeNotify(IntPtr p); int UnregisterControlChangeNotify(IntPtr p); int GetChannelCount(out uint n);
  int SetMasterVolumeLevel(float l, ref Guid g); int SetMasterVolumeLevelScalar(float l, ref Guid g);
  int GetMasterVolumeLevel(out float l); int GetMasterVolumeLevelScalar(out float l);
  int SetChannelVolumeLevel(uint c, float l, ref Guid g); int SetChannelVolumeLevelScalar(uint c, float l, ref Guid g);
  int GetChannelVolumeLevel(uint c, out float l); int GetChannelVolumeLevelScalar(uint c, out float l);
  int SetMute([MarshalAs(UnmanagedType.Bool)] bool m, ref Guid g); int GetMute(out bool m);
}
[Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPropertyStore { int GetCount(out uint n); int GetAt(uint i, out PropKey k); int GetValue(ref PropKey k, IntPtr v); int SetValue(ref PropKey k, IntPtr v); int Commit(); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
  int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o);
  int OpenPropertyStore(int access, out IPropertyStore s); int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id); int GetState(out int s);
}
[Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceCollection { int GetCount(out uint n); int Item(uint i, out IMMDevice d); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator { int EnumAudioEndpoints(int flow, int mask, out IMMDeviceCollection c); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] public class MMDeviceEnumerator { }
public static class Vol {
  public static string[] Run(string match, double set) {
    var list = new System.Collections.Generic.List<string>();
    var en = (IMMDeviceEnumerator)new MMDeviceEnumerator();
    IMMDeviceCollection col; en.EnumAudioEndpoints(0, 1, out col);
    uint n; col.GetCount(out n);
    for (uint i = 0; i < n; i++) {
      IMMDevice d; col.Item(i, out d);
      IPropertyStore ps; d.OpenPropertyStore(0, out ps);
      var key = new PropKey { fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), pid = 14 };
      IntPtr pv = Marshal.AllocCoTaskMem(24);
      ps.GetValue(ref key, pv);
      string name = Marshal.PtrToStringUni(Marshal.ReadIntPtr(pv, 8));
      Guid iid = typeof(IAudioEndpointVolume).GUID; object o;
      d.Activate(ref iid, 23, IntPtr.Zero, out o);
      var v = (IAudioEndpointVolume)o; float level; v.GetMasterVolumeLevelScalar(out level);
      bool muted; v.GetMute(out muted);
      if (match != "" && name.IndexOf(match, StringComparison.OrdinalIgnoreCase) >= 0 && set >= 0) {
        Guid g = Guid.Empty; v.SetMasterVolumeLevelScalar((float)set, ref g);
        list.Add("SET " + name + " " + level.ToString("0.000") + " -> " + set.ToString("0.000"));
      } else list.Add(name + " " + level.ToString("0.000") + (muted ? " muted" : ""));
    }
    return list.ToArray();
  }
}
"@
[Vol]::Run($Match, $Set)
