import torch.functional as F
from torch.utils.data import Dataset, DataLoader
import pandas as pd
import numpy as np
import torch
import shutil
from tqdm import tqdm
from torch.nn.functional import cross_entropy
import os
from matplotlib import pyplot as plt
from sklearn.metrics import confusion_matrix
import sklearn.metrics as metrics

# import foolbox as fb


class jointDataset(Dataset):

    def __init__(self, testset, adversialset):

        self.testset = testset
        self.adversialset = adversialset

    def __len__(self):
        return len(
            self.testset_dir
        )  # one or the other should be fine as they both have same length

    def __getitem__(self, idx):
        test_image = torch.load(self.testset.__getitem__(idx))
        adversial_image = torch.load(self.adversialset.__getitem__(idx))
        return test_image, adversial_image


class AdversarialDataset(Dataset):
    def __init__(self, test_set, image_dir):
        """
        Args:
            testloader (DataLoader): DataLoader with og images that will be used to create adversial images
            image_dir (str): Directory where to put adversial images with attack type in name
        """
        # self.annotations_df = pd.read_csv(
        #    image_dir + "/annotations.csv"
        # )  # columns ["adversial_id", "image_id","adversial_label", "sub_label", "oracle_label"]
        self.image_dir = image_dir
        self.test_set = test_set
        self.epsilons_fgsm = []  # epsilon values for which attacks where created
        self.epsilons_saliency = []  # epsilon values for which attacks where created

        self.epsilon = None
        self.attack_type = None

    def __len__(self):
        return len(self.annotations_df.index)

    def __getitem__(self, idx):
        """This will simply get the adversial image."""

        image_name = f"image_{idx}.pt"

        img_path = (
            self.image_dir + f"/{self.attack_type}/" + f"/{self.epsilon}/" + image_name
        )
        # label = self.annotations_df.iloc[idx]["og_label"]

        # Load the image
        image = torch.load(img_path)

        return image

    def get_annotations(self, epsilon, attack_type):
        annotations_path = (
            self.image_dir + f"/{attack_type}/" + f"/{epsilon}/" + "annotations.csv"
        )
        return pd.read_csv(annotations_path)

    # Fast-Gradient Sign Method for computing adversial attacks
    def attack_FGSM(self, substitute, oracle, epsilon, batch_size=32, fb=False):
        """Create the Black box attack and returns the success rate on the subsititute and the transferability to the oracle"""

        device = (
            torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        )

        # should ensure you always go into this specific folder for the attack and can create multiple epsilon attacks for a given attack type
        self.attack_type = "fgsm"
        rows = []
        image_path = self.image_dir + "/" + self.attack_type + "/" + str(epsilon)
        # create folder for images
        if os.path.exists(image_path):
            shutil.rmtree(image_path)
        os.makedirs(image_path, exist_ok=True)

        testloader = DataLoader(self.test_set, batch_size=batch_size, shuffle=False)
        # create the adversial images
        for idx_0, (x, y) in enumerate(
            tqdm(
                testloader,
                desc="Creating and saving attacks",
                leave=False,
                unit="batch",
            )
        ):
            x, y = x.to(device), y.to(device)
            substitute.to(device)

            # if fb:
            #    x_adv = fb.attacks.FGSM()
            # else:
            x_adv = self.FGSM(model=substitute, x=x, y=y, epsilon=epsilon)

            # get the true labels to compare later
            y_substitute = substitute.predict(x)
            y_oracle = oracle.predict(x)

            for idx_1 in range(x.shape[0]):

                x_adv_idx = x_adv[
                    idx_1
                ]  # test if this gets the correct image depending on all sizes

                y_substitute_idx = y_substitute[idx_1]
                y_oracle_idx = y_oracle[idx_1]
                y_idx = y[idx_1]

                new_idx = idx_1 + idx_0 * batch_size
                new_image_id = f"image_{new_idx}.pt"
                new_image_path = image_path + "/" + new_image_id

                x_adv_idx.to(device)

                # get the true labels to compare later
                y_substitute_adv_idx = substitute.predict(x_adv_idx)
                y_oracle_adv_idx = oracle.predict(x_adv_idx)

                rows.append(
                    {
                        "adversial_id": new_idx,
                        "image_id": new_idx,
                        "true_label": y_idx.item(),
                        "sub_label": y_substitute_idx.item(),
                        "oracle_label": y_oracle_idx.item(),
                        "adv_sub_label": y_substitute_adv_idx.item(),
                        "adv_oracle_label": y_oracle_adv_idx.item(),
                    }
                )

                # save new image
                torch.save(
                    x_adv_idx,
                    new_image_path,
                )

        annotations_df = pd.DataFrame(rows)
        annotations_df.to_csv(image_path + "/annotations.csv", index=False)

        # return transferability and attack success rate on oracle and substitute
        # only check the attack if the model get the true label correct

        total_correct_sub = annotations_df["true_label"] == annotations_df["sub_label"]
        substitute_success_frac = (
            annotations_df["sub_label"]
            != annotations_df["adv_sub_label"]
            & (annotations_df["true_label"] == annotations_df["sub_label"])
        ).sum()

        total_correct_oracle = (
            annotations_df["true_label"] == annotations_df["oracle_label"]
        )
        oracle_success_frac = (
            annotations_df["oracle_label"]
            != annotations_df["adv_oracle_label"]
            & (annotations_df["true_label"] == annotations_df["oracle_label"])
        ).sum()
        substitute_success = substitute_success_frac / total_correct_sub
        oracle_success_succss = oracle_success_frac / total_correct_oracle

        print(
            f"Epsilon: {epsilon} Substitute attack success rate: {substitute_success}, fraction: {substitute_success_frac}/{total_correct_sub}"
        )
        print(
            f"Epsilon: {epsilon} Oracle attack success rate: {oracle_success_succss}, fraction: {oracle_success_frac}/{total_correct_oracle}"
        )

        # attack finished
        self.epsilons_fgsm.append(epsilon)
        self.epsilons_fgsm = list(set(self.epsilons_fgsm))
        return substitute_success, oracle_success_succss

    def test_transferability(self, oracle, epsilon, attack_type, batch_size=32):

        device = (
            torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        )

        oracle_true = []
        oracle_adversial = []

        self.annotations_df = self.get_annotations(
            epsilon, attack_type
        )  # so we know the length of the adversial dataset

        # update epsilon so goes into correct folder
        self.epsilon = epsilon
        self.attack_type = attack_type
        jointD = jointDataset(self.test_set, self)
        jointLoader = DataLoader(jointD, batch_size=batch_size, shuffle=False)
        # create the adversial images
        for idx, (x_test), (x_adv) in tqdm(
            jointLoader, desc="Requesting the models", leave=False, unit="batch"
        ):
            x_test, x_adv = x_test.to(device), x_adv.to(device)

            # get the label
            oracle_true.append(oracle.predict(x_test))
            oracle_adversial.append(oracle.predict(x_adv))

        oracle_true_np = np.array(oracle_true)
        oracle_adversial_np = np.array(oracle_adversial)

        # Find the indices where the arrays differ
        num_same_positions = np.sum(oracle_true_np != oracle_adversial_np)

        # get annotations

        # Count the number of different positions
        attack_success_frac = len(num_same_positions)
        attack_sucess = attack_success_frac / len(self.annotations_df.index)
        print(
            f"Oracle attack success rate: {attack_sucess}, fraction: {attack_success_frac}/{len(self.annotations_df.index)}"
        )

        return attack_sucess

    def FGSM(self, model, x, y, epsilon):
        # gradient with respect to input only to save compute
        x.requires_grad = True
        for param in model.parameters():
            param.requires_grad = False

        output = model(x)
        loss = cross_entropy(output, y)
        loss.backward()

        # set it back to prevent bugs
        for param in model.parameters():
            param.requires_grad = True

        # create adversial examples
        x_adv = x + epsilon * torch.sign(x.grad)
        return x_adv

    # Saliency attack from Papernot et al.
    def saliency_map():
        # need to remember to put attack type and the epislon thing as in FGSM
        pass

    def show_adversial_progress(self, model, attack_type, num_output):
        # function used to show the adversial image created
        # image id goes from 0 to 9849
        fig, ax = plt.subplots(figsize=(15, 15), nrows=1, ncols=5)

        #
        if attack_type == "fgsm":
            epsilons = self.epsilons_fgsm
        else:
            epsilons = self.epsilons_saliency

        # set attack type to get correct folder
        self.attack_type = attack_type
        epsilons.sort()  # sort the list for future use

        # this should sorted in increasing order
        for i, epsilon in enumerate(epsilons):

            # set epsilon value to go into correct folder
            self.epsilon = epsilon
            annotations_df = self.get_annotations(epsilon, attack_type)
            cm = self.get_confusion_matrices(
                y_pred=annotations_df["sub_label"],
                y_adv=annotations_df["adv_sub_label"],
            )

            cm_display = metrics.ConfusionMatrixDisplay(
                confusion_matrix=confusion_matrix,
                display_labels=list(range(num_output)),
            )

            # TODO make this work for CIFAR-10 since we remove the first channel to have 28x28
            # ax[i].imshow(image.squeeze(), cmap="gray")
            # ax[i].set_title(f"epsilon = {epsilon} confusion matrix")

        plt.show()

    def show_cm_progress(self, model, image_id, attack_type):
        # function used to show the model accuracy given epsilon values for a agiven attack type
        # image id goes from 0 to 9849
        fig, ax = plt.subplots(figsize=(15, 15), nrows=1, ncols=5)

        #
        if attack_type == "fgsm":
            epsilons = self.epsilons_fgsm
        else:
            epsilons = self.epsilons_saliency

        # set attack type to get correct folder
        self.attack_type = attack_type
        epsilons.sort()  # sort the list for future use

        # this should sorted in increasing order
        for i, epsilon in enumerate(epsilons):
            # set epsilon value to go into correct folder
            self.epsilon = epsilon
            image = self.__getitem__(image_id)
            pred = model.predict(image).item()

            # TODO make this work for CIFAR-10 since we remove the first channel to have 28x28
            ax[i].imshow(image.squeeze(), cmap="gray")
            ax[i].set_title(f"epsilon = {epsilon} pred={pred}")
            ax[i].axis("off")

    def get_confusion_matrices(self, y_pred, y_adv):
        # get the confusion matrices for the oracle and the substitute after adverisal attacks

        return confusion_matrix(np.array(y_pred), np.array(y_adv))
