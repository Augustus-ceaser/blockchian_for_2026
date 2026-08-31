# LAN Roadshow Setup

Use a dedicated router or phone hotspot configured as a Windows `Private` network. Connect the host and at least one other physical device to the same network.

1. Start Docker Desktop.
2. Open PowerShell in the project directory.
3. Run `.\scripts\get_roadshow_network.ps1 -Select`.
4. If multiple physical adapters are shown, rerun with `-InterfaceAlias`.
5. Run `.\scripts\prepare_lan_roadshow.ps1`.
6. Open the printed `/join` URL on each device.

The normal command preserves the current business chain. Only `-Reset` invokes the existing formal reset.

Firewall access is separate and explicit:

```powershell
.\scripts\configure_lan_firewall.ps1 -Action Add -Port 8080 -Confirmed
```

Run that command only from an elevated PowerShell after confirming the selected network is Private. Never disable Windows Firewall.

Stop without deleting volumes:

```powershell
.\scripts\stop_lan_roadshow.ps1
```

Do not run `docker compose down -v`; it deletes persisted demonstration data.
