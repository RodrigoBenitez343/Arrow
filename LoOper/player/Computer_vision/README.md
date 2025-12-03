# Vision & Text Detection System 📸🧠

Welcome to the **Vision & Text Detection System**, a Python-based tool that leverages **YOLO (You Only Look Once)** for real-time object detection and **Tesseract OCR** for text recognition. This project captures live screen data, detects objects, extracts text, and organizes it into readable formats. It's designed for multi-process handling, ensuring both vision and text detection processes run seamlessly in parallel.

## Features 🚀
- **Real-Time Object Detection**: Uses a YOLO model to detect objects from the screen and logs their coordinates.
- **Text Extraction**: Extracts text from detected objects' bounding boxes using Tesseract OCR.
- **Data Storage**: Object coordinates and labels are stored as JSON files, making it easy to track detections over time.
- **Multi-Process Functionality**: Runs vision detection and text updating concurrently for improved performance.
- **Configurable**: You can replace the YOLO model with your preferred version.

## System Requirements 🛠️
- Python 3.7+
- Required Python Libraries:
  - `ultralytics`
  - `numpy`
  - `mss`
  - `pillow`
  - `pytesseract`
  - `json`
  - `logging`
  - `multiprocessing`
  - `re`
  - `time`

## Installation Guide 📝

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/bauerhartmut/yolov8-Computervision.git
   ```
   
2. **Install libs**:
   ```bash
   pip install ultralytics mss pytesseract
   ```
   
4. **Install Tesseract OCR**:
   - Download and install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract).
   - Make sure to set the Tesseract path in the code correctly. Example:
     ```python
     pytesseract.pytesseract.tesseract_cmd = r'C:/Program Files/Tesseract-OCR/tesseract.exe'
     ```

5. **Configure the YOLO Model**:
   - Download my YOLOv8 Model from Huggingface [Computer_Vision_1.5.3](https://huggingface.co/BauerHartmut/yolov8_Computer_Vision_1.5.3)
   - get the label_description.json file from Huggingface [Label_description.json](https://huggingface.co/BauerHartmut/yolov8_Computer_Vision_1.5.3/blob/main/label_description.json)

## How It Works 🛠️

### Vision Class 📷
The `Vision` class is responsible for handling object detection. It uses the YOLO model to analyze screen captures and detect objects. The results are saved in JSON files.

- **Screen Capture**: Captures the live screen using the `mss` library.
- **Object Detection**: YOLO analyzes the captured screen and detects objects.
- **JSON Output**: Detected objects, along with their coordinates, are stored in `model_view_output/label.json` and respective label files.

### Api Class 🌐
The `Api` class handles reading the JSON outputs, retrieving object coordinates, and performing OCR to extract text.

- **Retrieve Labels & Positions**: Methods to get label data and object coordinates from JSON files.
- **Text Extraction**: Uses Tesseract OCR to extract text from the detected objects' bounding boxes.
- **Text Cleanup**: Removes non-ASCII characters from the extracted text.

### Multi-Process Flow 🔄
This project runs two separate processes in parallel:
1. **Vision Process**: Constantly runs object detection on the screen.
2. **Text Update Process**: Continuously extracts and updates text found in the bounding boxes.

### Example Output 📂
The system creates a directory `model_view_output/` with the following files:
- **labels.json**: Contains the count of detected objects for each label.
- **{label}.json**: Stores the coordinates for each detected object of that label.
- **text.json**: Stores coordinates of text-containing objects.
- **sumirize.json**: A summary of the extracted text and corresponding coordinates.

## How to Run ▶️
1. Ensure Tesseract is properly installed and its path is correctly set.
2. Start the system by running the main script:
   ```bash
   python main.py
   ```

   This will:
   - Initiate the **Vision Process** for real-time object detection.
   - Start the **Text Updating Process** to extract and log text from detected objects.

## File Structure 📁

```
📦 Vision-Text-Detection
 ┣ 📂 model_view_output  # Stores output JSON files
 ┣ 📜 main.py            # Main script that runs the processes
 ┣ 📜 README.md          # Project documentation
 ┣ 📜 requirements.txt   # List of required Python libraries
 ┗ 📜 vision_api.py      # Contains Vision and Api classes
```

## Customization Options 🎛️
- **YOLO Model**: Replace `vision_model = "Computer_Vision_1.3.0.onnx"` with your own model path.
- **Tesseract Path**: Update the Tesseract OCR path if installed in a different directory.
- **Detection Intervals**: Modify the `time.sleep()` intervals in the `start_vision()` and `updating_text()` methods for faster/slower detection cycles.

## Contributing 🤝
Feel free to fork the repository, submit issues, and open pull requests for improvements. All contributions are welcome!

## License 📄
This project is licensed under the MIT License.
