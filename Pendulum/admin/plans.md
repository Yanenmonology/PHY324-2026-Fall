Sketch sheet for pipeline:

## Milestones:
  - [ ] IMU array to MCU communication and data transfer success.
  - [ ] MCU to SD card data dump success.
  - [ ] MCU to Raspberry pi timestamp sync success.
  - [ ] Vision process Halcon/opencv
  - [ ] Data process.

## Major todos:
- [ ] Data process on MCU or only record raw data
- [ ] Reconstruction of 3D (partially done)

## uncertainties(calibration):
  -[] Sensor level raw self test and calibration
  -[] MBS board level (13 imu array) calibration and test / per run based
  -[] Maybe time stamp also need to be calibrated on MCU and Pi level.
  ### not yet coded:
  -[] Camera vision(pixiel, location, size, verticals)
  -[] Time delta between camera and MBS board
  -[] Mass, CoM,
  -[] local gravity (from MBS board and Natural Resources Canada’s gravity data)

## Remarks:
In the control over network functionality group in src, secret.h is not being uploaded. OTA token is randomly generated.