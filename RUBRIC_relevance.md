# AV-relevance labelling rubric (v1)

The single question every paper in this corpus gets: **is this paper about
autonomous *road* vehicles?** Answer is binary: **core** or **adjacent**.
This rubric is the spec. The automated classifier (`scripts/classify.py`)
and every human label (`data/relevance_labels*.json`) are measured against
it. If a real paper doesn't fit cleanly here, the rubric is wrong -- fix
the rubric, then re-decide.

## The rule

Label **core** if **either**:

- **(a) Contribution.** The paper's *primary* contribution is aimed at
  automating a road vehicle or the driving task -- perception, prediction,
  planning, decision-making, control, mapping/localisation, simulation,
  datasets/benchmarks, V2X/cooperative driving, ADAS functions
  (ACC/AEB/lane-keeping/FCW), automated-driving safety, validation,
  verification, HMI, or teleoperation -- **for a car, truck, or bus on
  roads**. "Primary" means: remove the driving framing and the paper loses
  its point.

- **(b) Setting.** The paper's *own* method is evaluated in a driving
  setting -- on a driving dataset (nuScenes, KITTI, Waymo Open, Argoverse,
  BDD100K, ...), in a driving simulator (CARLA, ...), or on real
  on-road/traffic data -- as a main experiment, not a single side table.

Otherwise label **adjacent**.

## Hard adjacents (these override (a) and (b))

- **Non-road platforms.** Aerial / UAV / drone / quadrotor / eVTOL,
  underwater / marine / surface vessel, spacecraft / satellite / planetary
  rover, legged / quadruped / humanoid robots, robot manipulation /
  grasping. A method "also demoed on a car" does not rescue a paper whose
  subject is one of these.
- **Automotive mechanical / hardware engineering with no learning or
  software contribution** -- chassis/suspension/powertrain/tyre/brake
  hardware design, drivetrain configurations, classical vehicle-dynamics
  control. (A learning-based method applied to these *is* core.)
- **Intelligent-transportation topics that are not the vehicle** --
  traffic-signal timing, travel-demand / mode-choice modelling,
  public-transit / rail / freight scheduling, toll / congestion pricing,
  EV charging-infrastructure siting, crash-frequency / injury-severity
  statistics, vehicular-network communication protocols, roadside traffic
  surveillance / speed enforcement.

## Not enough on its own (-> adjacent)

- **Motivation-only mention.** A general-purpose method (object detection,
  segmentation, tracking, SLAM, depth, domain adaptation, a new optimizer,
  ...) that lists "autonomous driving" among possible applications, or runs
  one KITTI table among many benchmarks, but whose contribution is the
  general method. If the same paper would be equally at home in a corpus
  about medical imaging or generic robotics, it's adjacent.
- **A single AV keyword** in an otherwise unrelated abstract.

## No-abstract papers

Many venue listings (DBLP-sourced IV / ITSC / T-ITS / older ICRA-IROS) give
title + authors only. Decide on the title alone, the way a human skims a
proceedings table of contents. Proceedings titles are usually specific
enough; when a bare title is genuinely undecidable, label **adjacent**
(the corpus errs toward precision) and note it.

## Worked examples

| Title (abridged) | Label | Why |
|---|---|---|
| Decentralized Cooperative Planning for Automated Vehicles | core | (a) planning, for road vehicles |
| Turning decisions for end-to-end vehicle control | core | (a) control of the driving task |
| Analytical performance bounds of autonomous emergency brake systems | core | (a) an ADAS function |
| Direct 3D Detection of Vehicles (ITSC) | core | (a) perception whose subject is road vehicles |
| A VT-HMM Framework for Countdown-Timer Traffic-Light State Estimation | core | (a) driving-infrastructure perception |
| nuScenes: A Multimodal Dataset for Autonomous Driving | core | (a) a driving dataset |
| Depth Anything: A Foundation Model for Monocular Depth | adjacent | general method; driving is one of many uses |
| Stereo R-CNN Based 3D Object Detection for Autonomous Driving | core | (b) contribution framed and evaluated for driving |
| SiamMask: Fast Online Object Tracking and Segmentation | adjacent | general tracker; no driving contribution/eval |
| Physically Realizable Adversarial Examples for LiDAR Object Detection | core | attack is developed and evaluated in the AV detection setting |
| An LSTM Approach to Temporal 3D Object Detection in LiDAR Point Clouds | borderline -> core if the only eval is a driving dataset, else adjacent | apply (b) strictly |
| Autonomous Navigation of Unmanned Aerial Vehicles in GPS-Denied Environments | adjacent | non-road platform (hard adjacent) |
| Stanford Doggo: An Open-Source Quasi-Direct-Drive Quadruped | adjacent | legged robot (hard adjacent); "Drive" is a drivetrain term |
| Handling and Stability Control of AFS and DYC for Distributed-Drive EV | adjacent | mechanical vehicle-dynamics control, no learning/software contribution |
| Deep Reinforcement Learning for Active Front Steering Control | core | learning-based method applied to the same hardware problem |
| Traffic Signal Timing Optimization for Arterial Corridors | adjacent | ITS topic that is not the vehicle |
| Cooperative Adaptive Cruise Control of Connected and Automated Vehicles | core | (a) an ADAS/driving function |
| Vehicle Routing Problem with Time Windows: A GNN Approach | adjacent | logistics/OR, not vehicle automation |
| A Survey of Federated Learning for Connected and Automated Vehicles | core | survey whose subject is the AV application |
| Federated Learning for Image Classification (mentions "e.g. self-driving") | adjacent | motivation-only mention |

## Change log

- **v1 (2026-08):** first written-down version. Supersedes the informal
  "explicit AV phrase OR the LLM says the tech is directly applied"
  description that lived only in `classify.py`'s docstring and the About page.
