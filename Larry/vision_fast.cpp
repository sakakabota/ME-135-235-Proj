// vision_fast.cpp
// C++ port of vision.py: YOLOv8-seg person silhouette + 64x64 pixelation
// from a USB camera, using OpenCV's DNN module (no PyTorch needed at runtime).
//
// One-time setup (you only need Python/Ultralytics for this export step):
//     pip install ultralytics
//     yolo export model=yolov8n-seg.pt format=onnx opset=12
//     # produces yolov8n-seg.onnx in the current dir
//
// Build (macOS, OpenCV from Homebrew >= 4.7):
//     clang++ -std=c++17 -O3 vision_fast.cpp -o vision_fast \
//         $(pkg-config --cflags --libs opencv4)
//
// Build (Linux):
//     g++ -std=c++17 -O3 vision_fast.cpp -o vision_fast \
//         $(pkg-config --cflags --libs opencv4)
//
// Run:
//     ./vision_fast            # expects yolov8n-seg.onnx next to it
//
// Controls:
//     q       quit
//     s       save current 64x64 frame as silhouette_64.png
//     SPACE   pause / resume
//     [ / ]   decrease / increase YOLO confidence threshold

#include <opencv2/opencv.hpp>
#include <opencv2/dnn.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

constexpr int   CAMERA_INDEX     = 0;
constexpr int   FRAME_WIDTH      = 640;
constexpr int   FRAME_HEIGHT     = 480;
constexpr int   OUTPUT_SIZE      = 64;
constexpr int   INFER_IMGSZ      = 640;
constexpr int   PERSON_CLASS_ID  = 0;
constexpr int   NUM_CLASSES      = 80;
constexpr int   NUM_MASK_COEFFS  = 32;
constexpr float NMS_THRESHOLD    = 0.45f;
constexpr float MASK_THRESHOLD   = 0.5f;
const std::string MODEL_PATH     = "yolov8n-seg.onnx";

cv::VideoCapture openCamera(int idx) {
    const int backends[] = {cv::CAP_AVFOUNDATION, cv::CAP_V4L2, cv::CAP_ANY};
    for (int b : backends) {
        cv::VideoCapture cap(idx, b);
        if (cap.isOpened()) {
            cap.set(cv::CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH);
            cap.set(cv::CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT);
            return cap;
        }
    }
    throw std::runtime_error("Could not open camera index " + std::to_string(idx));
}

// Resize+pad to a square keeping aspect ratio. Returns scale and the (x, y)
// padding so we can map predictions back to the original frame.
cv::Mat letterbox(const cv::Mat& src, int new_size,
                  float& scale, int& pad_x, int& pad_y) {
    const int w = src.cols;
    const int h = src.rows;
    scale = std::min(static_cast<float>(new_size) / w,
                     static_cast<float>(new_size) / h);
    const int new_w = static_cast<int>(std::round(w * scale));
    const int new_h = static_cast<int>(std::round(h * scale));
    pad_x = (new_size - new_w) / 2;
    pad_y = (new_size - new_h) / 2;

    cv::Mat resized;
    cv::resize(src, resized, cv::Size(new_w, new_h));
    cv::Mat padded(new_size, new_size, src.type(), cv::Scalar(114, 114, 114));
    resized.copyTo(padded(cv::Rect(pad_x, pad_y, new_w, new_h)));
    return padded;
}

// In-place sigmoid for a CV_32F Mat.
void sigmoidInPlace(cv::Mat& m) {
    cv::Mat tmp = -m;
    cv::exp(tmp, tmp);
    m = 1.0 / (1.0 + tmp);
}

}  // namespace

int main() {
    std::cout << "Loading " << MODEL_PATH << "..." << std::endl;
    cv::dnn::Net net;
    try {
        net = cv::dnn::readNetFromONNX(MODEL_PATH);
    } catch (const cv::Exception& e) {
        std::cerr << "Failed to load model: " << e.what() << "\n"
                  << "Did you export the .onnx? Run:\n"
                  << "  yolo export model=yolov8n-seg.pt format=onnx opset=12\n";
        return 1;
    }
    net.setPreferableBackend(cv::dnn::DNN_BACKEND_OPENCV);
    net.setPreferableTarget(cv::dnn::DNN_TARGET_CPU);

    cv::VideoCapture cap = openCamera(CAMERA_INDEX);
    cv::Mat kernel = cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(5, 5));

    float confidence = 0.40f;
    bool  paused     = false;
    cv::Mat last_frame;
    int n_frames = 0;
    auto t_start = std::chrono::steady_clock::now();

    std::cout << "q=quit, s=save 64x64, SPACE=pause, [/]=conf -/+\n";

    while (true) {
        cv::Mat frame;
        if (!paused) {
            if (!cap.read(frame) || frame.empty()) {
                std::cerr << "Camera read failed\n";
                break;
            }
            last_frame = frame.clone();
        } else {
            frame = last_frame.clone();
        }

        const int H = frame.rows;
        const int W = frame.cols;

        // 1. Letterbox + build network input blob.
        float scale; int pad_x, pad_y;
        cv::Mat lb = letterbox(frame, INFER_IMGSZ, scale, pad_x, pad_y);
        cv::Mat blob = cv::dnn::blobFromImage(
            lb, 1.0 / 255.0, cv::Size(INFER_IMGSZ, INFER_IMGSZ),
            cv::Scalar(), /*swapRB=*/true, /*crop=*/false);

        // 2. Forward pass. YOLOv8-seg has two outputs (order may vary):
        //    det:   (1, 4 + NC + NM, A)        e.g. (1, 116, 8400)
        //    proto: (1, NM, ph, pw)            e.g. (1, 32, 160, 160)
        net.setInput(blob);
        std::vector<cv::Mat> outs;
        std::vector<std::string> out_names = net.getUnconnectedOutLayersNames();
        net.forward(outs, out_names);

        cv::Mat det_out, proto_out;
        for (auto& m : outs) {
            if      (m.dims == 3) det_out   = m;
            else if (m.dims == 4) proto_out = m;
        }
        if (det_out.empty() || proto_out.empty()) {
            std::cerr << "Unexpected model outputs\n";
            break;
        }

        const int row_size      = det_out.size[1];   // 4 + NC + NM = 116
        const int num_proposals = det_out.size[2];   // 8400 typ.
        const int proto_h       = proto_out.size[2]; // 160 typ.
        const int proto_w       = proto_out.size[3]; // 160 typ.

        // Wrap det_out as a 2D Mat (row_size x num_proposals) and transpose
        // so each row becomes a single proposal.
        cv::Mat det(row_size, num_proposals, CV_32F, det_out.ptr<float>(0));
        cv::Mat det_t;
        cv::transpose(det, det_t);   // (num_proposals, row_size)

        // proto: (NM, ph*pw) so we can do mask = coeffs * proto -> (1, ph*pw).
        cv::Mat proto_2d(NUM_MASK_COEFFS, proto_h * proto_w, CV_32F,
                         proto_out.ptr<float>(0));

        // 3. Filter by class==person and confidence.
        std::vector<cv::Rect>            cand_boxes;
        std::vector<cv::Rect2f>          cand_boxes_lb;   // letterbox coords
        std::vector<float>               cand_scores;
        std::vector<std::vector<float>>  cand_coeffs;
        cand_boxes.reserve(64);

        for (int i = 0; i < num_proposals; ++i) {
            const float* row = det_t.ptr<float>(i);
            const float person_score = row[4 + PERSON_CLASS_ID];
            if (person_score < confidence) continue;

            const float cx = row[0];
            const float cy = row[1];
            const float bw = row[2];
            const float bh = row[3];
            const float lx = cx - bw * 0.5f;
            const float ly = cy - bh * 0.5f;

            // Map letterbox coords -> original frame coords.
            const float ox = (lx - pad_x) / scale;
            const float oy = (ly - pad_y) / scale;
            const float ow = bw / scale;
            const float oh = bh / scale;

            cv::Rect orig(static_cast<int>(std::round(ox)),
                          static_cast<int>(std::round(oy)),
                          static_cast<int>(std::round(ow)),
                          static_cast<int>(std::round(oh)));
            orig &= cv::Rect(0, 0, W, H);
            if (orig.area() <= 0) continue;

            std::vector<float> coeffs(NUM_MASK_COEFFS);
            for (int k = 0; k < NUM_MASK_COEFFS; ++k) {
                coeffs[k] = row[4 + NUM_CLASSES + k];
            }

            cand_boxes.push_back(orig);
            cand_boxes_lb.emplace_back(lx, ly, bw, bh);
            cand_scores.push_back(person_score);
            cand_coeffs.push_back(std::move(coeffs));
        }

        std::vector<int> keep_idx;
        cv::dnn::NMSBoxes(cand_boxes, cand_scores, confidence,
                          NMS_THRESHOLD, keep_idx);

        // 4. Build a combined silhouette by ORing each kept person mask.
        cv::Mat silhouette = cv::Mat::zeros(H, W, CV_8UC1);
        std::vector<cv::Rect> kept_boxes;
        kept_boxes.reserve(keep_idx.size());

        for (int idx : keep_idx) {
            cv::Mat coeff(1, NUM_MASK_COEFFS, CV_32F, cand_coeffs[idx].data());
            cv::Mat mask_flat = coeff * proto_2d;          // (1, ph*pw)
            cv::Mat mask = mask_flat.reshape(1, proto_h);  // (ph, pw)
            sigmoidInPlace(mask);

            // proto-space mask -> letterbox-space mask -> frame-space mask.
            cv::Mat mask_lb;
            cv::resize(mask, mask_lb, cv::Size(INFER_IMGSZ, INFER_IMGSZ),
                       0, 0, cv::INTER_LINEAR);

            const int crop_w = INFER_IMGSZ - 2 * pad_x;
            const int crop_h = INFER_IMGSZ - 2 * pad_y;
            cv::Mat mask_content = mask_lb(cv::Rect(pad_x, pad_y, crop_w, crop_h));

            cv::Mat mask_orig;
            cv::resize(mask_content, mask_orig, cv::Size(W, H),
                       0, 0, cv::INTER_LINEAR);

            // Threshold and restrict to this detection's bbox so neighbouring
            // person masks don't bleed into each other.
            cv::Mat bin;
            cv::compare(mask_orig, MASK_THRESHOLD, bin, cv::CMP_GT);
            cv::Mat box_only = cv::Mat::zeros(H, W, CV_8UC1);
            box_only(cand_boxes[idx]).setTo(255);
            cv::bitwise_and(bin, box_only, bin);

            cv::bitwise_or(silhouette, bin, silhouette);
            kept_boxes.push_back(cand_boxes[idx]);
        }

        // 5. Cleanup, contours, and a re-filled "clean" silhouette.
        cv::morphologyEx(silhouette, silhouette, cv::MORPH_CLOSE, kernel);

        std::vector<std::vector<cv::Point>> contours;
        cv::findContours(silhouette, contours,
                         cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
        const double min_area = 0.002 * H * W;   // 0.2% of frame
        std::vector<std::vector<cv::Point>> people;
        people.reserve(contours.size());
        for (auto& c : contours) {
            if (cv::contourArea(c) >= min_area) people.push_back(std::move(c));
        }

        cv::Mat clean = cv::Mat::zeros(H, W, CV_8UC1);
        cv::drawContours(clean, people, -1, cv::Scalar(255), cv::FILLED);

        cv::Mat preview = frame.clone();
        cv::drawContours(preview, people, -1, cv::Scalar(0, 255, 0), 2);
        for (const auto& b : kept_boxes) {
            cv::rectangle(preview, b, cv::Scalar(255, 128, 0), 1);
        }

        // 6. Pixelate -> 64x64 (the downsize IS the pixelation).
        cv::Mat small_img;
        cv::resize(clean, small_img, cv::Size(OUTPUT_SIZE, OUTPUT_SIZE),
                   0, 0, cv::INTER_AREA);
        cv::threshold(small_img, small_img, 96, 255, cv::THRESH_BINARY);

        cv::Mat chunky;
        cv::resize(small_img, chunky, cv::Size(H, H), 0, 0, cv::INTER_NEAREST);

        // HUD.
        n_frames++;
        const auto t_now   = std::chrono::steady_clock::now();
        const double secs  = std::chrono::duration<double>(t_now - t_start).count();
        const double fps   = n_frames / std::max(secs, 1e-6);

        std::ostringstream hud;
        hud << "people: " << people.size()
            << "  conf: " << std::fixed << std::setprecision(2) << confidence
            << "  fps: "  << std::fixed << std::setprecision(1) << fps;
        if (paused) hud << "  [PAUSED]";
        cv::putText(preview, hud.str(), cv::Point(10, 24),
                    cv::FONT_HERSHEY_SIMPLEX, 0.6, cv::Scalar(0, 255, 0), 2);

        cv::imshow("camera + contours", preview);
        cv::imshow("silhouette",        clean);
        cv::imshow("64x64 pixelated",   chunky);

        const int key = cv::waitKey(1) & 0xFF;
        if      (key == 'q') break;
        else if (key == 's') {
            cv::imwrite("silhouette_64.png", small_img);
            std::cout << "saved silhouette_64.png\n";
        }
        else if (key == ' ')  paused     = !paused;
        else if (key == '[')  confidence = std::max(0.05f, confidence - 0.05f);
        else if (key == ']')  confidence = std::min(0.95f, confidence + 0.05f);
    }

    cap.release();
    cv::destroyAllWindows();
    return 0;
}
