#pragma once

#define DEBUG_BAUD_RATE 115200  
#define DYNAMIXEL_BAUD_RATE 57600

#define DAMPER_PIN 5
#define WRIST_ID 0           

#define CMD_GET_CUR "GET_CUR"    
#define CMD_SET_ANG "SET_ANG"    
#define CMD_SET_DMP "SET_DMP"
#define CMD_GET_ANG "GET_ANG"

//I2C Device Map (Hardware Verification)
// 0x28: IMU Inertial Measurement Unit
// 0x40: INA219 Current Sensor