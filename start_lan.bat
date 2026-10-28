@echo off
rem Roundtable for tablets / phones on the home Wi-Fi (docs/LAN.md).
rem Same as start.bat, but also listens on the local network. Other devices must log in with the
rem access password set on this computer (page button "Tablet"). Use only on trusted networks (home Wi-Fi).
rem Windows may ask once whether Python may use the network: tick "Private networks" and allow.
setlocal
set "RT_HOST=0.0.0.0"
set "ROUNDTABLE_LAN=1"
call "%~dp0start.bat"
