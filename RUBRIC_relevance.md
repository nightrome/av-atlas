# AV-relevance labelling rubric (v1)

Every paper in this corpus gets one question: **is this paper about autonomous
*road* vehicles?** The answer is binary: **AV** or **non-AV**.

This rubric is the spec. The automated classifier (`scripts/classify.py`) and every
human label (`data/relevance_labels*.json`) are measured against it. If a real paper
doesn't fit cleanly, the rubric is wrong. Fix the rubric first, then re-decide the
paper.

## The rule

Label a paper **AV** if **either** of these holds:

- **(a) Contribution.** The paper's *main* contribution is aimed at automating a road
  vehicle or the driving task, for a car, truck or bus on roads. That covers
  perception, prediction, planning, decision-making, control, mapping and
  localisation, simulation, datasets and benchmarks, V2X and cooperative driving, ADAS
  functions (ACC, AEB, lane-keeping, FCW), automated-driving safety, validation,
  verification, HMI and teleoperation. "Main" means: if you remove the driving framing,
  the paper loses its point.

- **(b) Setting.** The paper's *own* method is evaluated in a driving setting: on a
  driving dataset (nuScenes, KITTI, Waymo Open, Argoverse, BDD100K, ...), in a driving
  simulator (CARLA, ...) or on real on-road or traffic data. It has to be a main
  experiment, not a single side table.

Otherwise, label it **non-AV**.

## Always non-AV

These override (a) and (b).

- **Non-road platforms.** Aerial, UAV, drone, quadrotor and eVTOL; underwater, marine
  and surface vessels; spacecraft, satellites and planetary rovers; legged, quadruped
  and humanoid robots; robot manipulation and grasping. A method that was "also demoed
  on a car" doesn't rescue a paper whose subject is one of these.
- **Automotive mechanical or hardware engineering with no learning or software
  contribution.** That means chassis, suspension, powertrain, tyre and brake hardware
  design, drivetrain configurations, and classical vehicle-dynamics control. (A
  learning-based method applied to these *is* AV.)
- **Intelligent-transportation topics that aren't about the vehicle.** Traffic-signal
  timing, travel-demand and mode-choice modelling, public-transit, rail and freight
  scheduling, toll and congestion pricing, EV charging-station siting, crash-frequency
  and injury-severity statistics, vehicular-network communication protocols, and roadside
  traffic surveillance and speed enforcement.

## Not enough on its own (label non-AV)

- **A motivation-only mention.** A general-purpose method (object detection,
  segmentation, tracking, SLAM, depth, domain adaptation, a new optimizer, ...) that
  lists "autonomous driving" among its possible applications, or runs one KITTI table
  among many benchmarks, but whose contribution is the general method. If the same
  paper would sit just as well in a corpus about medical imaging or generic robotics,
  it's non-AV.
- **A single AV keyword** in an otherwise unrelated abstract.

## Papers with no abstract

Many venue listings (DBLP-sourced IV, ITSC and T-ITS, and older ICRA and IROS) give only
a title and authors. Decide from the title alone, the way a person skims a proceedings
table of contents. Titles in proceedings are usually specific enough. When a bare title
really can't be decided, label it **non-AV** (the corpus errs toward precision) and note
it.

## Worked examples

| Title (abridged) | Label | Why |
|---|---|---|
| Decentralized Cooperative Planning for Automated Vehicles | AV | (a) planning, for road vehicles |
| Turning decisions for end-to-end vehicle control | AV | (a) control of the driving task |
| Analytical performance bounds of autonomous emergency brake systems | AV | (a) an ADAS function |
| Direct 3D Detection of Vehicles (ITSC) | AV | (a) perception whose subject is road vehicles |
| A VT-HMM Framework for Countdown-Timer Traffic-Light State Estimation | AV | (a) driving-infrastructure perception |
| nuScenes: A Multimodal Dataset for Autonomous Driving | AV | (a) a driving dataset |
| Depth Anything: A Foundation Model for Monocular Depth | non-AV | general method; driving is one of many uses |
| Stereo R-CNN Based 3D Object Detection for Autonomous Driving | AV | (b) contribution framed and evaluated for driving |
| SiamMask: Fast Online Object Tracking and Segmentation | non-AV | general tracker; no driving contribution or evaluation |
| Physically Realizable Adversarial Examples for LiDAR Object Detection | AV | attack is developed and evaluated in the AV detection setting |
| An LSTM Approach to Temporal 3D Object Detection in LiDAR Point Clouds | borderline: AV if the only evaluation is a driving dataset, otherwise non-AV | apply (b) strictly |
| Autonomous Navigation of Unmanned Aerial Vehicles in GPS-Denied Environments | non-AV | non-road platform (always non-AV) |
| Stanford Doggo: An Open-Source Quasi-Direct-Drive Quadruped | non-AV | legged robot (always non-AV); "Drive" is a drivetrain term |
| Handling and Stability Control of AFS and DYC for Distributed-Drive EV | non-AV | mechanical vehicle-dynamics control, no learning or software contribution |
| Deep Reinforcement Learning for Active Front Steering Control | AV | learning-based method applied to the same hardware problem |
| Traffic Signal Timing Optimization for Arterial Corridors | non-AV | an intelligent-transportation topic that isn't the vehicle |
| Cooperative Adaptive Cruise Control of Connected and Automated Vehicles | AV | (a) an ADAS or driving function |
| Vehicle Routing Problem with Time Windows: A GNN Approach | non-AV | logistics and operations research, not vehicle automation |
| A Survey of Federated Learning for Connected and Automated Vehicles | AV | survey whose subject is the AV application |
| Federated Learning for Image Classification (mentions "e.g. self-driving") | non-AV | motivation-only mention |

## Change log

- **v1 (2026-08):** the first written-down version. It replaces the informal "explicit AV
  phrase OR the LLM says the tech is directly applied" description that only existed in
  `classify.py`'s docstring and the About page.
