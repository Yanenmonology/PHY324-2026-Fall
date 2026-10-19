Sketch sheet for pipeline:

## Thing to consider:
### vision part:
1. Timimng - shutters: Both pi, iphone dslrs use rolling shutters 5-30ms. Mass moves up to 2m/s so different row of frames have different moments. <br>  <code> Need todo: consider the calibration of the LED on the same position only for sync -> sync_geometrically</code>
2. Timing - clock: no common clock is shared between 4 devices, but the sensor board and pi are synced already via wifi on timestamp. <br> <code>Need to do: linear offsets on different clocks -> Cali_intrisinc</code>
3. Timing - frame rate vary: nominal fps is not trustworthy, also fps may differes from camera to camera<br> <code> Need to do: use there own time stamps individually -> sync_exterinsic</code>
4. Lens and focus: DLSR and iphone may swich focal length and lens(especially iphone). <br> <code>Need to do: lock focal point and turn of auto focus -> Cali_intrisinc</code>
5. Light source: room light may licker. <br> <code> Need to do: use non flicker source light</code>

---

## Milestones:
  - [ ] IMU array to MCU communication and data transfer success.
  - [ ] MCU to SD card data dump success.
  - [ ] MCU to Raspberry pi timestamp sync success.
  - [ ] Vision process Halcon/opencv
  - [ ] Data process.

---

## Major todos:
- [ ] Data process on MCU or only record raw data
- [ ] Reconstruction of 3D (partially done)

---

## uncertainties(calibration):
  - [ ] Sensor level raw self test and calibration
  - [ ] MBS board level (13 imu array) calibration and test / per run based
  - [ ] Maybe time stamp also need to be calibrated on MCU and Pi level.
  ### not yet coded:
  - [ ] Camera vision(pixiel, location, size, verticals)
  - [ ] Time delta between camera and MBS board
  - [ ] Mass, CoM,
  - [ ] local gravity (from MBS board and Natural Resources Canada’s gravity data)

---
## Objects:
1. Camera:
   - Property: Name, type, exposure_ms, intrinsic_matrix, clibration_matrix, flicker_hz, led_threshold, led_roi
   - Functions: Calibration_intrinsic, sync(maybe not? depends on location)

---
## Remarks:
In the control over network functionality group in src, secret.h is not being uploaded. OTA token is randomly generated.